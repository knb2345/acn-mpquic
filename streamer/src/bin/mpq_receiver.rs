//! Frame receiver (QUIC server). Reads every unidirectional stream: a 16-byte
//! part header {frame_id, offset, length, frame_size}, then `length` bytes.
//! When a stream ends, its byte range is added to the frame; the frame is
//! complete when the ranges from any streams cover [0, frame_size). Closes the
//! connection once every frame of the workload is complete, or
//! --max-duration-s after the first stream.
//!
//! Output (--out): frame_id,frame_size,parts,first_byte_ts_us,complete_ts_us

use std::cell::RefCell;
use std::collections::HashMap;
use std::error::Error;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use clap::Parser;
use log::{error, info, warn, LevelFilter};
use tquic::{
    CongestionControlAlgorithm, Config, Connection, Endpoint, MultipathAlgorithm, PacketInfo,
    TlsConfig, TransportHandler,
};
use tquic_tools::QuicSocket;

use streamer::*;

#[derive(Parser)]
#[clap(about = "Receive frames over multipath TQUIC and log frame completion times")]
struct Opt {
    #[clap(long)]
    listen: SocketAddr,
    #[clap(long)]
    cert: String,
    #[clap(long)]
    key: String,
    #[clap(long)]
    enable_multipath: bool,
    #[clap(long, default_value = "minrtt")]
    multipath_algor: MultipathAlgorithm,
    #[clap(long, default_value = "cubic")]
    cc: CongestionControlAlgorithm,
    /// Workload trace, to know how many frames to expect.
    #[clap(long)]
    frames: PathBuf,
    /// Per-frame receive log (csv).
    #[clap(long)]
    out: PathBuf,
    #[clap(long, default_value_t = 120)]
    max_duration_s: u64,
    #[clap(long)]
    log_file: PathBuf,
    #[clap(long, default_value = "info")]
    log_level: LevelFilter,
    #[clap(long)]
    qlog_dir: Option<PathBuf>,
    /// Optional live log for the demo dashboard: one line "frame_id,complete_ts_us"
    /// written (unbuffered) as each frame completes. Does not change recv.csv.
    #[clap(long)]
    live_log: Option<PathBuf>,
}

/// A part stream being received.
#[derive(Default)]
struct PartRx {
    header: Vec<u8>,
    payload: u64,
}

struct FrameRx {
    size: u32,
    cover: Coverage,
    parts: u32,
    first_byte_ts: u64,
    complete_ts: Option<u64>,
}

#[derive(Default)]
struct State {
    conn: Option<u64>,
    closed: bool,
    first_stream: Option<Instant>,
    /// Keyed by (connection index, stream id): with A1 the sender opens two
    /// connections, and their stream ids overlap.
    parts: HashMap<(u64, u64), PartRx>,
    frames: HashMap<u32, FrameRx>,
    complete: usize,
    qlog_dir: Option<PathBuf>,
    live: Option<File>,
}

struct Handler {
    st: Rc<RefCell<State>>,
    buf: Vec<u8>,
}

impl TransportHandler for Handler {
    fn on_conn_created(&mut self, conn: &mut Connection) {
        if let Some(dir) = &self.st.borrow().qlog_dir {
            let id = conn.trace_id().to_string();
            match File::create(dir.join(format!("{id}.qlog"))) {
                Ok(f) => conn.set_qlog(Box::new(f), "mpq_receiver".into(), id),
                Err(e) => warn!("qlog: {e}"),
            }
        }
    }

    fn on_conn_established(&mut self, conn: &mut Connection) {
        let mut st = self.st.borrow_mut();
        if st.conn.is_none() {
            st.conn = conn.index();
            info!("{} established", conn.trace_id());
        }
    }

    fn on_conn_closed(&mut self, conn: &mut Connection) {
        let mut st = self.st.borrow_mut();
        if st.conn == conn.index() {
            info!("{} closed", conn.trace_id());
            st.closed = true;
        }
    }

    fn on_stream_created(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_stream_readable(&mut self, conn: &mut Connection, stream_id: u64) {
        let mut st = self.st.borrow_mut();
        st.first_stream.get_or_insert_with(Instant::now);
        let key = (conn.index().unwrap_or(0), stream_id);
        loop {
            match conn.stream_read(stream_id, &mut self.buf) {
                Ok((n, fin)) => {
                    let now = now_us();
                    let part = st.parts.entry(key).or_default();
                    let need = HEADER_LEN - part.header.len();
                    part.header.extend_from_slice(&self.buf[..n.min(need)]);
                    part.payload += n.saturating_sub(need) as u64;
                    if part.header.len() == HEADER_LEN && part.payload == 0 && n > 0 {
                        // Header just completed: note the frame's first byte.
                        let h = PartHeader::decode(&part.header);
                        st.frames.entry(h.frame_id).or_insert(FrameRx {
                            size: h.frame_size,
                            cover: Coverage::default(),
                            parts: 0,
                            first_byte_ts: now,
                            complete_ts: None,
                        });
                    }
                    if !fin {
                        continue;
                    }
                    let part = st.parts.remove(&key).unwrap();
                    if part.header.len() < HEADER_LEN {
                        warn!("stream {stream_id} ended without a full header");
                        break;
                    }
                    let h = PartHeader::decode(&part.header);
                    if part.payload != h.length as u64 {
                        warn!("stream {stream_id}: {} payload bytes, header says {}", part.payload, h.length);
                    }
                    let f = st.frames.entry(h.frame_id).or_insert(FrameRx {
                        size: h.frame_size,
                        cover: Coverage::default(),
                        parts: 0,
                        first_byte_ts: now,
                        complete_ts: None,
                    });
                    f.parts += 1;
                    f.cover.add(h.offset, h.offset + part.payload as u32);
                    if f.complete_ts.is_none() && f.cover.complete(f.size) {
                        f.complete_ts = Some(now);
                        let fid = h.frame_id;
                        if let Some(w) = st.live.as_mut() {
                            let _ = writeln!(w, "{fid},{now}");
                        }
                        st.complete += 1;
                    }
                    break;
                }
                Err(tquic::Error::Done) => break,
                Err(e) => {
                    warn!("stream {stream_id} read: {e:?}");
                    break;
                }
            }
        }
    }

    fn on_stream_writable(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_stream_closed(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_new_token(&mut self, _conn: &mut Connection, _token: Vec<u8>) {}
}

fn main() -> Result<(), Box<dyn Error>> {
    let opt = Opt::parse();
    SplitLogger::install(opt.log_level, &opt.log_file, Vec::new())?;
    let expected = load_frames(&opt.frames)?.len();

    let mut config = Config::new()?;
    config.set_max_idle_timeout(60_000);
    config.set_initial_max_data(64 << 20);
    config.set_initial_max_stream_data_uni(8 << 20);
    config.set_initial_max_streams_uni(1 << 20);
    config.set_congestion_control_algorithm(opt.cc);
    config.enable_multipath(opt.enable_multipath);
    config.set_multipath_algorithm(opt.multipath_algor);
    config.set_active_connection_id_limit(4);
    config.set_tls_config(TlsConfig::new_server_config(&opt.cert, &opt.key, vec![ALPN.to_vec()], false)?);

    let mut poll = mio::Poll::new()?;
    let sock = Rc::new(QuicSocket::new(&opt.listen, poll.registry())?);
    let live = match &opt.live_log {
        Some(p) => {
            let mut f = File::create(p)?;
            writeln!(f, "frame_id,complete_ts_us")?;
            Some(f)
        }
        None => None,
    };
    let st = Rc::new(RefCell::new(State { qlog_dir: opt.qlog_dir.clone(), live, ..Default::default() }));
    let handler = Handler { st: st.clone(), buf: vec![0u8; 65536] };
    let mut endpoint = Endpoint::new(Box::new(config), true, Box::new(handler), sock.clone());
    info!("listening on {}", sock.local_addr());

    let term = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, term.clone())?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, term.clone())?;

    let mut closing_since: Option<Instant> = None;
    let mut buf = vec![0u8; 65536];
    let mut events = mio::Events::with_capacity(1024);

    loop {
        endpoint.process_connections()?;
        let (done, closed, conn, timed_out) = {
            let st = st.borrow();
            (
                st.complete >= expected,
                st.closed,
                st.conn,
                st.first_stream.is_some_and(|t| t.elapsed() > Duration::from_secs(opt.max_duration_s)),
            )
        };
        if closed || closing_since.is_some_and(|t| t.elapsed() > Duration::from_secs(2)) {
            break;
        }
        if closing_since.is_none() && (done || timed_out || term.load(Ordering::Relaxed)) {
            info!("closing: {} of {expected} frames complete", st.borrow().complete);
            if let Some(c) = conn.and_then(|i| endpoint.conn_get_mut(i)) {
                let _ = c.close(true, 0, b"done");
            }
            closing_since = Some(Instant::now());
            continue;
        }

        if let Err(e) = poll.poll(&mut events, endpoint.timeout().map(|t| t.min(Duration::from_millis(100)))) {
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

    let st = st.borrow();
    let mut out = BufWriter::new(File::create(&opt.out)?);
    writeln!(out, "frame_id,frame_size,parts,first_byte_ts_us,complete_ts_us")?;
    let mut ids: Vec<_> = st.frames.keys().copied().collect();
    ids.sort_unstable();
    for id in ids {
        let f = &st.frames[&id];
        let done = f.complete_ts.map_or(String::new(), |t| t.to_string());
        writeln!(out, "{id},{},{},{},{done}", f.size, f.parts, f.first_byte_ts)?;
    }
    out.flush()?;
    info!("{} of {expected} frames complete", st.complete);
    log::logger().flush();
    Ok(())
}
