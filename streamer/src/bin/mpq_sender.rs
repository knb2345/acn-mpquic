//! Frame sender supporting TQUIC schedulers (S0-S4, R1) and frame-aware
//! schedulers F1, F2, A1, and R2.
//!
//! Output (--out): seq,frame_id,frame_type,size_bytes,stream_id,capture_ts_us,send_start_us,rescued

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use clap::Parser;
use log::{error, info, warn, LevelFilter};
use tquic::{
    CongestionControlAlgorithm, Config, Connection, Endpoint, MultipathAlgorithm, PacketInfo,
    TlsConfig, TransportHandler,
};
use tquic_tools::QuicSocket;

use streamer::*;

#[derive(Parser)]
#[clap(about = "Send a workload frame trace over multipath TQUIC")]
struct Opt {
    #[clap(long)]
    connect: SocketAddr,
    #[clap(long, value_delimiter = ',', required = true)]
    local_addresses: Vec<IpAddr>,
    #[clap(long)]
    enable_multipath: bool,
    #[clap(long, default_value = "minrtt")]
    multipath_algor: MultipathAlgorithm,
    #[clap(long, value_enum, default_value = "tquic")]
    policy: Policy,
    #[clap(long, default_value = "cubic")]
    cc: CongestionControlAlgorithm,
    #[clap(long)]
    frames: PathBuf,
    #[clap(long, default_value_t = 0)]
    clip_offset: usize,
    #[clap(long, default_value_t = 1000)]
    warmup_ms: u64,
    #[clap(long, default_value_t = 120)]
    max_duration_s: u64,
    #[clap(long)]
    out: PathBuf,
    #[clap(long)]
    frame_path_log: Option<PathBuf>,
    #[clap(long)]
    paths_log: Option<PathBuf>,
    #[clap(long)]
    log_file: PathBuf,
    #[clap(long, default_value = "info")]
    log_level: LevelFilter,
    #[clap(long)]
    qlog_dir: Option<PathBuf>,
    /// True path rates for R2
    #[clap(long, default_value_t = 1600)]
    a_rate_kbit: u64,
    #[clap(long, default_value_t = 1600)]
    b_rate_kbit: u64,
    #[clap(long)]
    replay_csv: Option<PathBuf>,
}

#[derive(Clone, Debug)]
struct InflightPart {
    frame_id: u32,
    frame_type: char,
    frame_size: usize,
    offset: u32,
    length: u32,
    path_idx: usize, // 0 = Path A, 1 = Path B
    sent_ts_us: u64,
    rescued: bool,
    /// The part is unfinished until its stream closes (all bytes acknowledged).
    conn_idx: u64,
    stream_id: u64,
}

#[derive(Default)]
struct State {
    conns: Vec<u64>,
    established: Vec<Instant>,
    closed_count: usize,
    extra_paths: Vec<SocketAddr>,
    remote: Option<SocketAddr>,
    /// (conn_idx, stream_id) -> remaining unwritten bytes
    pending: HashMap<(u64, u64), Bytes>,
    qlog_dir: Option<PathBuf>,
    /// (conn_idx, stream_id) of streams fully acknowledged since the last check.
    closed: HashSet<(u64, u64)>,
}

struct Handler {
    st: Rc<RefCell<State>>,
}

impl TransportHandler for Handler {
    fn on_conn_created(&mut self, conn: &mut Connection) {
        if let Some(dir) = &self.st.borrow().qlog_dir {
            let id = conn.trace_id().to_string();
            match File::create(dir.join(format!("{id}.qlog"))) {
                Ok(f) => conn.set_qlog(Box::new(f), "mpq_sender".into(), id),
                Err(e) => warn!("qlog: {e}"),
            }
        }
    }

    fn on_conn_established(&mut self, conn: &mut Connection) {
        let mut st = self.st.borrow_mut();
        if let Some(idx) = conn.index() {
            if !st.conns.contains(&idx) {
                st.conns.push(idx);
                st.established.push(Instant::now());
            }
        }
        info!("{} established", conn.trace_id());
        if st.conns.len() == 1 && !st.extra_paths.is_empty() {
            let remote = st.remote.unwrap();
            for local in st.extra_paths.clone() {
                match conn.add_path(local, remote) {
                    Ok(_) => info!("added path {local} -> {remote}"),
                    Err(e) => error!("add_path {local}: {e:?}"),
                }
            }
        }
    }

    fn on_conn_closed(&mut self, conn: &mut Connection) {
        info!("{} closed", conn.trace_id());
        self.st.borrow_mut().closed_count += 1;
    }

    fn on_stream_created(&mut self, _conn: &mut Connection, _stream_id: u64) {}
    fn on_stream_readable(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_stream_writable(&mut self, conn: &mut Connection, stream_id: u64) {
        let Some(cidx) = conn.index() else { return };
        let mut st = self.st.borrow_mut();
        let Some(tail) = st.pending.remove(&(cidx, stream_id)) else {
            let _ = conn.stream_want_write(stream_id, false);
            return;
        };
        match conn.stream_write(stream_id, tail.clone(), true) {
            Ok(n) if n < tail.len() => {
                st.pending.insert((cidx, stream_id), tail.slice(n..));
            }
            Ok(_) => {
                let _ = conn.stream_want_write(stream_id, false);
            }
            Err(tquic::Error::Done) => {
                st.pending.insert((cidx, stream_id), tail);
            }
            Err(e) => warn!("stream {stream_id} write: {e:?}"),
        }
    }

    fn on_stream_closed(&mut self, conn: &mut Connection, stream_id: u64) {
        if let Some(cidx) = conn.index() {
            self.st.borrow_mut().closed.insert((cidx, stream_id));
        }
    }
    fn on_new_token(&mut self, _conn: &mut Connection, _token: Vec<u8>) {}
}

fn log_paths(conn: &mut Connection, w: &mut BufWriter<File>) -> std::io::Result<()> {
    let ts = now_us();
    let tuples: Vec<_> = conn.paths_iter().collect();
    for t in tuples {
        if let Ok(s) = conn.get_path_stats(t.local, t.remote) {
            writeln!(
                w,
                "{ts},{},{},{},{},{},{},{},{},{},{}",
                t.local, s.srtt, s.min_rtt, s.rttvar, s.final_cwnd, s.sent_bytes, s.acked_bytes,
                s.lost_bytes, s.pacing_rate, s.in_slow_start as u8
            )?;
        }
    }
    Ok(())
}

fn write_part_stream(
    conn: &mut Connection,
    st: &Rc<RefCell<State>>,
    pref_path: Option<(SocketAddr, SocketAddr)>,
    frame: &Frame,
    offset: usize,
    length: usize,
    track: Option<(usize, usize)>,
) -> Result<u64, Box<dyn Error>> {
    // Non-incremental priority (urgency 0, incremental = false) preserves strict frame order.
    let sid = conn.stream_uni_new(0, false)?;
    // track = (connection key, path index): count this part in U_p of that path.
    if let Some((ck, path)) = track {
        send_tracker().lock().unwrap().register(ck, sid, path, (HEADER_LEN + length) as u64);
    }
    if let Some(p) = pref_path {
        let _ = conn.stream_set_path_preference(sid, Some(p));
    }
    let mut data = vec![0u8; HEADER_LEN + length];
    let h = PartHeader {
        frame_id: frame.id,
        offset: offset as u32,
        length: length as u32,
        frame_size: frame.size as u32,
    };
    data[..HEADER_LEN].copy_from_slice(&h.encode());
    let data = Bytes::from(data);
    let n = match conn.stream_write(sid, data.clone(), true) {
        Ok(n) => n,
        Err(tquic::Error::Done) => 0,
        Err(e) => return Err(e.into()),
    };
    if n < data.len() {
        if let Some(cidx) = conn.index() {
            st.borrow_mut().pending.insert((cidx, sid), data.slice(n..));
        }
        conn.stream_want_write(sid, true)?;
    }
    Ok(sid)
}

fn main() -> Result<(), Box<dyn Error>> {
    let opt = Opt::parse();
    let mut routes = Vec::new();
    if let Some(p) = &opt.frame_path_log {
        routes.push(Route::csv(FRAME_PATH_TARGET, p, "ts_us,stream_id,path_id,offset,len,fin,local"));
    }
    SplitLogger::install(opt.log_level, &opt.log_file, routes)?;
    let frames = rotate(&load_frames(&opt.frames)?, opt.clip_offset);

    let is_a1 = opt.policy == Policy::A1;
    let use_mp = if is_a1 { false } else { opt.enable_multipath };
    // For F1, F2, R2 on a single multipath connection, use RoundRobin underneath so
    // TQUIC visits both paths and honors stream_set_path_preference (hook a).
    let tquic_algor = match opt.policy {
        Policy::F1 | Policy::F2 | Policy::R2 => MultipathAlgorithm::RoundRobin,
        _ => opt.multipath_algor,
    };

    let mut config = Config::new()?;
    config.set_max_idle_timeout(60_000);
    config.set_initial_max_data(64 << 20);
    config.set_initial_max_stream_data_uni(8 << 20);
    config.set_initial_max_streams_uni(1 << 20);
    config.set_congestion_control_algorithm(opt.cc);
    config.enable_multipath(use_mp);
    config.set_multipath_algorithm(tquic_algor);
    config.set_active_connection_id_limit(4);
    let mut tls = TlsConfig::new_client_config(vec![ALPN.to_vec()], false)?;
    tls.set_verify(false);
    config.set_tls_config(tls);

    let mut poll = mio::Poll::new()?;
    let mut sock = QuicSocket::new(&SocketAddr::new(opt.local_addresses[0], 0), poll.registry())?;
    let mut extra_paths = Vec::new();
    for ip in &opt.local_addresses[1..] {
        extra_paths.push(sock.add(&SocketAddr::new(*ip, 0), poll.registry())?);
    }
    let sock = Rc::new(sock);
    {
        // Which connection each local address belongs to (A1: one connection per path).
        let mut t = send_tracker().lock().unwrap();
        for (i, ip) in opt.local_addresses.iter().enumerate() {
            t.set_conn_of_ip(*ip, if is_a1 { i.min(1) } else { 0 });
        }
    }
    let st = Rc::new(RefCell::new(State {
        extra_paths: if is_a1 { Vec::new() } else { extra_paths.clone() },
        remote: Some(opt.connect),
        qlog_dir: opt.qlog_dir.clone(),
        ..Default::default()
    }));
    let mut endpoint = Endpoint::new(Box::new(config), false, Box::new(Handler { st: st.clone() }), sock.clone());

    let local_a = sock.local_addr();
    endpoint.connect(local_a, opt.connect, Some("example.org"), None, None, None)?;
    if is_a1 && !extra_paths.is_empty() {
        endpoint.connect(extra_paths[0], opt.connect, Some("example.org"), None, None, None)?;
    }

    let term = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, term.clone())?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, term.clone())?;

    let mut out = BufWriter::new(File::create(&opt.out)?);
    writeln!(out, "seq,frame_id,frame_type,size_bytes,stream_id,capture_ts_us,send_start_us,rescued")?;
    let mut paths_log = match &opt.paths_log {
        Some(p) => {
            let mut w = BufWriter::new(File::create(p)?);
            writeln!(w, "ts_us,local,srtt_us,min_rtt_us,rttvar_us,cwnd,sent_bytes,acked_bytes,lost_bytes,pacing_rate,in_slow_start")?;
            Some(w)
        }
        None => None,
    };

    let mut rate_a = RateEstimator::new(opt.a_rate_kbit);
    let mut rate_b = RateEstimator::new(opt.b_rate_kbit);
    let oracle = TrueRateOracle::new(opt.a_rate_kbit, opt.b_rate_kbit, opt.replay_csv.as_deref());
    let mut inflight: Vec<InflightPart> = Vec::new();
    let mut last_ack_progress_ts = [now_us(); 2];
    let mut last_acked_total = [0u64; 2];

    let warmup = Duration::from_millis(opt.warmup_ms);
    let mut next = 0usize;
    let mut seq = 0usize;
    let mut start: Option<(Instant, u64)> = None;
    let mut next_paths_log = Instant::now();
    let mut buf = vec![0u8; 65536];
    let mut events = mio::Events::with_capacity(1024);

    loop {
        let (conns, established_first) = {
            let st = st.borrow();
            (st.conns.clone(), st.established.first().copied())
        };
        let mut wait = None;
        let ready = if is_a1 { conns.len() >= 2 } else { !conns.is_empty() };

        if ready {
            if let Some(est) = established_first {
                if start.is_none() && Instant::now() >= est + warmup {
                    start = Some((Instant::now(), now_us()));
                    info!("streaming {} frames (policy={:?})", frames.len(), opt.policy);
                }
            }

            let now = now_us();
            // Gather per-path stats for paths A (index 0) and B (index 1)
            let mut path_tuples: Vec<(usize, SocketAddr, SocketAddr, u64, u64, u64, u64, u64, u64)> = Vec::new();
            if is_a1 {
                for (pidx, &cidx) in conns.iter().take(2).enumerate() {
                    if let Some(c) = endpoint.conn_get_mut(cidx) {
                        if let Some(t) = c.paths_iter().next() {
                            if let Ok(s) = c.get_path_stats(t.local, t.remote) {
                                path_tuples.push((pidx, t.local, t.remote, s.srtt, s.min_rtt, s.rttvar, s.sent_bytes, s.acked_bytes, s.lost_bytes));
                            }
                        }
                    }
                }
            } else if let Some(c) = endpoint.conn_get_mut(conns[0]) {
                let tuples: Vec<_> = c.paths_iter().collect();
                for t in tuples {
                    let pidx = if t.local.ip() == opt.local_addresses[0] { 0 } else { 1 };
                    if let Ok(s) = c.get_path_stats(t.local, t.remote) {
                        path_tuples.push((pidx, t.local, t.remote, s.srtt, s.min_rtt, s.rttvar, s.sent_bytes, s.acked_bytes, s.lost_bytes));
                    }
                }
            }

            let (u_a, u_b) = {
                let t = send_tracker().lock().unwrap();
                (t.unsent(0) as f64, t.unsent(1) as f64)
            };
            let mut est_a = PathEstimate { queued_bytes: u_a, rate_bps: rate_a.rate_bytes_per_sec(), srtt_us: 40_000.0, min_rtt_us: 40_000.0 };
            let mut est_b = PathEstimate { queued_bytes: u_b, rate_bps: rate_b.rate_bytes_per_sec(), srtt_us: 40_000.0, min_rtt_us: 40_000.0 };
            let mut addrs = [None::<(SocketAddr, SocketAddr)>; 2];
            // (bytes in flight, min_rtt_us) per path, for the bottleneck queue term.
            let mut bottleneck = [(0.0f64, 0.0f64); 2];
            let mut pto_us = [200_000u64; 2];

            for &(pidx, loc, rem, srtt, min_rtt, rttvar, sent_b, acked_b, lost_b) in &path_tuples {
                // Bytes ahead of a new frame on this path: those assigned but not yet
                // sent (U_p, from the send tracker) plus those queued at the
                // bottleneck. The bottleneck queue is the bytes in flight beyond one
                // round trip at the path's rate (inflight - R_p * min_rtt_p). This is
                // what the formula's (srtt_p - min_rtt_p) term estimates, but taken
                // now rather than from srtt, which is smoothed and lags behind; CUBIC's
                // window is several times the path's BDP here, so bytes leave the
                // send buffer at once and wait in the 200 ms bottleneck buffer.
                let unsent = send_tracker().lock().unwrap().unsent(pidx) as f64;
                let inflight = sent_b.saturating_sub(acked_b + lost_b) as f64;
                if acked_b > last_acked_total[pidx] {
                    last_acked_total[pidx] = acked_b;
                    last_ack_progress_ts[pidx] = now;
                }
                if pidx == 0 {
                    rate_a.update(now, acked_b);
                    est_a = PathEstimate {
                        queued_bytes: unsent,
                        rate_bps: rate_a.rate_bytes_per_sec(),
                        // srtt = min_rtt: the queueing term comes from bytes in flight
                        // (bottleneck_queue below), not from srtt.
                        srtt_us: min_rtt.max(1000) as f64,
                        min_rtt_us: min_rtt.max(1000) as f64,
                    };
                    bottleneck[pidx] = (inflight, min_rtt.max(1000) as f64);
                    addrs[0] = Some((loc, rem));
                    pto_us[0] = (srtt + 4 * rttvar.max(1000)).max(20_000);
                } else {
                    rate_b.update(now, acked_b);
                    est_b = PathEstimate {
                        queued_bytes: unsent,
                        rate_bps: rate_b.rate_bytes_per_sec(),
                        // srtt = min_rtt: the queueing term comes from bytes in flight
                        // (bottleneck_queue below), not from srtt.
                        srtt_us: min_rtt.max(1000) as f64,
                        min_rtt_us: min_rtt.max(1000) as f64,
                    };
                    bottleneck[pidx] = (inflight, min_rtt.max(1000) as f64);
                    addrs[1] = Some((loc, rem));
                    pto_us[1] = (srtt + 4 * rttvar.max(1000)).max(20_000);
                }
            }

            if opt.policy == Policy::R2 {
                if let Some((t0, _)) = start {
                    let (ra, rb) = oracle.rates_at_ms(t0.elapsed().as_millis() as u64);
                    est_a.rate_bps = ra;
                    est_b.rate_bps = rb;
                }
            }

            for (est, &(inflight, min_rtt_us)) in [&mut est_a, &mut est_b].into_iter().zip(bottleneck.iter()) {
                est.queued_bytes += bottleneck_queue_bytes(inflight, est.rate_bps, min_rtt_us);
            }

            // A part is finished once its stream has closed (all its bytes acknowledged).
            let closed = std::mem::take(&mut st.borrow_mut().closed);
            if !closed.is_empty() {
                inflight.retain(|p| !closed.contains(&(p.conn_idx, p.stream_id)));
            }

            // PTO rescue for F2, A1, R2: if a path has stalled for >= 1x PTO, resend
            // unrescued inflight parts on the alternate path as new streams.
            if matches!(opt.policy, Policy::F2 | Policy::A1 | Policy::R2) && addrs[0].is_some() && addrs[1].is_some() {
                let mut to_rescue = Vec::new();
                for part in &mut inflight {
                    let p = part.path_idx;
                    if !part.rescued && now.saturating_sub(part.sent_ts_us) >= pto_us[p]
                        && now.saturating_sub(last_ack_progress_ts[p]) >= pto_us[p]
                    {
                        part.rescued = true;
                        to_rescue.push(part.clone());
                    }
                }
                for part in to_rescue {
                    let alt = 1 - part.path_idx;
                    let fake_frame = Frame { id: part.frame_id, typ: part.frame_type, size: part.frame_size, pts_us: 0 };
                    let sid = if is_a1 {
                        endpoint.conn_get_mut(conns[alt])
                            .map(|c| write_part_stream(c, &st, None, &fake_frame, part.offset as usize, part.length as usize, Some((alt, alt))))
                            .transpose()?
                    } else {
                        endpoint.conn_get_mut(conns[0])
                            .map(|c| write_part_stream(c, &st, addrs[alt], &fake_frame, part.offset as usize, part.length as usize, Some((0, alt))))
                            .transpose()?
                    };
                    if let Some(sid) = sid {
                        writeln!(out, "{seq},{},{},{},{sid},{now},{now},1", part.frame_id, part.frame_type, part.frame_size)?;
                        seq += 1;
                    }
                }
            }

            if let Some((t0, m0)) = start {
                while next < frames.len() && Instant::now() >= t0 + Duration::from_micros(frames[next].1) {
                    let (f, t_rel) = &frames[next];
                    let ts = now_us();
                    let cap_ts = m0 + t_rel;

                    match opt.policy {
                        Policy::Tquic => {
                            let Some(conn) = endpoint.conn_get_mut(conns[0]) else { break };
                            let sid = write_part_stream(conn, &st, None, f, 0, f.size, None)?;
                            writeln!(out, "{seq},{},{},{},{sid},{cap_ts},{ts},0", f.id, f.typ, f.size)?;
                            seq += 1;
                        }
                        Policy::F1 => {
                            let Some(conn) = endpoint.conn_get_mut(conns[0]) else { break };
                            let choose_b = addrs[1].is_some()
                                && est_b.finish_time_us(f.size as f64) < est_a.finish_time_us(f.size as f64);
                            let pref = if choose_b { addrs[1] } else { addrs[0] };
                            let sid = write_part_stream(conn, &st, pref, f, 0, f.size, Some((0, choose_b as usize)))?;
                            if choose_b {
                                est_b.queued_bytes += f.size as f64;
                            } else {
                                est_a.queued_bytes += f.size as f64;
                            }
                            writeln!(out, "{seq},{},{},{},{sid},{cap_ts},{ts},0", f.id, f.typ, f.size)?;
                            seq += 1;
                        }
                        Policy::F2 | Policy::R2 | Policy::A1 => {
                            let split = if addrs[0].is_some() && addrs[1].is_some() {
                                balanced_split_bytes(est_a, est_b, f.size)
                            } else {
                                None
                            };

                            if let Some(s_a) = split {
                                let s_b = f.size - s_a;
                                let sid_a = if is_a1 {
                                    write_part_stream(endpoint.conn_get_mut(conns[0]).unwrap(), &st, None, f, 0, s_a, Some((0, 0)))?
                                } else {
                                    write_part_stream(endpoint.conn_get_mut(conns[0]).unwrap(), &st, addrs[0], f, 0, s_a, Some((0, 0)))?
                                };
                                writeln!(out, "{seq},{},{},{},{sid_a},{cap_ts},{ts},0", f.id, f.typ, f.size)?;
                                seq += 1;
                                inflight.push(InflightPart {
                                    frame_id: f.id, frame_type: f.typ, frame_size: f.size,
                                    offset: 0, length: s_a as u32, path_idx: 0,
                                    sent_ts_us: ts, rescued: false, conn_idx: conns[0], stream_id: sid_a,
                                });

                                let sid_b = if is_a1 {
                                    write_part_stream(endpoint.conn_get_mut(conns[1]).unwrap(), &st, None, f, s_a, s_b, Some((1, 1)))?
                                } else {
                                    write_part_stream(endpoint.conn_get_mut(conns[0]).unwrap(), &st, addrs[1], f, s_a, s_b, Some((0, 1)))?
                                };
                                writeln!(out, "{seq},{},{},{},{sid_b},{cap_ts},{ts},0", f.id, f.typ, f.size)?;
                                seq += 1;
                                inflight.push(InflightPart {
                                    frame_id: f.id, frame_type: f.typ, frame_size: f.size,
                                    offset: s_a as u32, length: s_b as u32, path_idx: 1,
                                    sent_ts_us: ts, rescued: false, conn_idx: if is_a1 { conns[1] } else { conns[0] }, stream_id: sid_b,
                                });
                                est_a.queued_bytes += s_a as f64;
                                est_b.queued_bytes += s_b as f64;
                            } else {
                                let choose_b = addrs[1].is_some()
                                    && est_b.finish_time_us(f.size as f64) < est_a.finish_time_us(f.size as f64);
                                let pidx = if choose_b { 1 } else { 0 };
                                let sid = if is_a1 {
                                    write_part_stream(endpoint.conn_get_mut(conns[pidx]).unwrap(), &st, None, f, 0, f.size, Some((pidx, pidx)))?
                                } else {
                                    write_part_stream(endpoint.conn_get_mut(conns[0]).unwrap(), &st, addrs[pidx], f, 0, f.size, Some((0, pidx)))?
                                };
                                writeln!(out, "{seq},{},{},{},{sid},{cap_ts},{ts},0", f.id, f.typ, f.size)?;
                                seq += 1;
                                inflight.push(InflightPart {
                                    frame_id: f.id, frame_type: f.typ, frame_size: f.size,
                                    offset: 0, length: f.size as u32, path_idx: pidx,
                                    sent_ts_us: ts, rescued: false, conn_idx: if is_a1 { conns[pidx] } else { conns[0] }, stream_id: sid,
                                });
                                if pidx == 0 { est_a.queued_bytes += f.size as f64; } else { est_b.queued_bytes += f.size as f64; }
                            }
                        }
                    }
                    next += 1;
                }
                if next < frames.len() {
                    wait = Some((t0 + Duration::from_micros(frames[next].1)).saturating_duration_since(Instant::now()));
                } else if t0.elapsed() > Duration::from_secs(opt.max_duration_s) {
                    warn!("giving up after {} s", opt.max_duration_s);
                    break;
                }
            } else if let Some(est) = established_first {
                wait = Some((est + warmup).saturating_duration_since(Instant::now()));
            }

            if let Some(w) = paths_log.as_mut() {
                if Instant::now() >= next_paths_log {
                    for &cidx in &conns {
                        if let Some(conn) = endpoint.conn_get_mut(cidx) {
                            log_paths(conn, w)?;
                        }
                    }
                    next_paths_log += Duration::from_millis(100);
                }
                let d = next_paths_log.saturating_duration_since(Instant::now());
                wait = Some(wait.map_or(d, |x| x.min(d)));
            }
        }

        endpoint.process_connections()?;
        if term.load(Ordering::Relaxed) || st.borrow().closed_count > 0 {
            break;
        }

        let timeout = match (endpoint.timeout(), wait) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        };
        if let Err(e) = poll.poll(&mut events, timeout) {
            if e.kind() != std::io::ErrorKind::Interrupted {
                return Err(e.into());
            }
        }
        for ev in events.iter().filter(|e| e.is_readable()) {
            loop {
                match sock.recv_from(&mut buf, ev.token()) {
                    Ok((len, local, remote)) => {
                        let info = PacketInfo { src: remote, dst: local, time: Instant::now() };
                        if let Err(e) = endpoint.recv(&mut buf[..len], &info) {
                            error!("recv: {e:?}");
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(e) => return Err(e.into()),
                }
            }
        }
        endpoint.on_timeout(Instant::now());
    }

    info!("sent {next} of {} frames", frames.len());
    out.flush()?;
    if let Some(w) = paths_log.as_mut() {
        w.flush()?;
    }
    log::logger().flush();
    Ok(())
}