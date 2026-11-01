//! Frame sender (QUIC server). After the receiver connects and a warm-up for
//! the extra paths, frame i is written at start + pts_i to a new
//! unidirectional stream, as one write with fin. Streams are created
//! non-incremental at one urgency, so TQUIC sends them in stream-id order
//! (oldest frame first). Each frame's deadline (start + pts_i + the playback
//! delay) is passed to the multipath scheduler; only deadline-aware
//! schedulers use it. Exits when the receiver closes the connection.
//!
//! Output (--out): frame_id,frame_type,size_bytes,stream_id,sched_ts_us,send_ts_us

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
#[clap(about = "Send a workload frame trace over multipath TQUIC, one stream per frame")]
struct Opt {
    #[clap(long)]
    listen: SocketAddr,
    #[clap(long)]
    cert: String,
    #[clap(long)]
    key: String,
    /// Workload trace (csv: frame_id,frame_type,size_bytes,pts_ms).
    #[clap(long)]
    frames: PathBuf,
    #[clap(long)]
    enable_multipath: bool,
    #[clap(long, default_value = "minrtt")]
    multipath_algor: MultipathAlgorithm,
    #[clap(long, default_value = "bbr")]
    cc: CongestionControlAlgorithm,
    /// Delay between handshake and the first frame, so extra paths are validated.
    #[clap(long, default_value_t = 1000)]
    warmup_ms: u64,
    /// Playback delay budget: frame i is due at start + pts_i + this.
    #[clap(long, default_value_t = 300)]
    deadline_ms: u64,
    /// Per-frame send log (csv).
    #[clap(long)]
    out: PathBuf,
    /// Per-STREAM-frame path log from TQUIC's team7 hook (csv).
    #[clap(long)]
    frame_path_log: Option<PathBuf>,
    /// Per-frame plans of the deadline/framepin/learned scheduler.
    #[clap(long)]
    plan_log: Option<PathBuf>,
    /// Per-path samples every 100 ms (training data for the learned scheduler).
    #[clap(long)]
    ml_features_log: Option<PathBuf>,
    /// The learned scheduler's capacity predictions.
    #[clap(long)]
    ml_pred_log: Option<PathBuf>,
    /// Quantile the learned scheduler uses (0.1, 0.2, 0.3 or 0.5); default:
    /// the one compiled into the model.
    #[clap(long)]
    learned_q: Option<f32>,
    #[clap(long)]
    log_file: PathBuf,
    #[clap(long, default_value = "info")]
    log_level: LevelFilter,
    #[clap(long)]
    qlog_dir: Option<PathBuf>,
}

#[derive(Default)]
struct State {
    conn: Option<u64>,
    established: Option<Instant>,
    closed: bool,
    /// Unwritten tail of a frame whose write was cut short (fin goes with it).
    pending: HashMap<u64, Bytes>,
    qlog_dir: Option<PathBuf>,
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
        if st.conn.is_none() {
            st.conn = conn.index();
            st.established = Some(Instant::now());
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

    fn on_stream_readable(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_stream_writable(&mut self, conn: &mut Connection, stream_id: u64) {
        let mut st = self.st.borrow_mut();
        let Some(tail) = st.pending.remove(&stream_id) else {
            let _ = conn.stream_want_write(stream_id, false);
            return;
        };
        match conn.stream_write(stream_id, tail.clone(), true) {
            Ok(n) if n < tail.len() => {
                st.pending.insert(stream_id, tail.slice(n..));
            }
            Ok(_) => {
                let _ = conn.stream_want_write(stream_id, false);
            }
            Err(e) => warn!("stream {stream_id} write: {e:?}"),
        }
    }

    fn on_stream_closed(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_new_token(&mut self, _conn: &mut Connection, _token: Vec<u8>) {}
}

fn main() -> Result<(), Box<dyn Error>> {
    let opt = Opt::parse();
    let mut routes = Vec::new();
    if let Some(p) = &opt.frame_path_log {
        routes.push(Route::csv(FRAME_PATH_TARGET, p, "ts_us,stream_id,path_id,offset,len,fin"));
    }
    if let Some(p) = &opt.plan_log {
        routes.push(Route { target: PLAN_TARGET, path: p.clone(), header: None, sep: ' ' });
    }
    if let Some(p) = &opt.ml_features_log {
        routes.push(Route::csv(ML_FEATURES_TARGET, p,
            "ts_us,path_id,sent,acked,lost,bw,srtt_us,min_rtt_us,latest_rtt_us,inflight,cwnd"));
    }
    if let Some(p) = &opt.ml_pred_log {
        routes.push(Route::csv(ML_PRED_TARGET, p, "ts_us,path_id,q10,q20,q30,q50,heuristic"));
    }
    SplitLogger::install(opt.log_level, &opt.log_file, routes)?;
    let frames = load_frames(&opt.frames)?;
    if let Some(q) = opt.learned_q {
        // Read by TQUIC's learned scheduler when the connection starts.
        std::env::set_var("TEAM7_LEARNED_Q", q.to_string());
    }

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
    let st = Rc::new(RefCell::new(State { qlog_dir: opt.qlog_dir.clone(), ..Default::default() }));
    let mut endpoint = Endpoint::new(Box::new(config), true, Box::new(Handler { st: st.clone() }), sock.clone());
    info!("listening on {}", sock.local_addr());

    let term = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, term.clone())?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, term.clone())?;

    let mut out = BufWriter::new(File::create(&opt.out)?);
    writeln!(out, "frame_id,frame_type,size_bytes,stream_id,sched_ts_us,send_ts_us")?;
    let warmup = Duration::from_millis(opt.warmup_ms);
    let mut next = 0;
    let mut start: Option<(Instant, u64)> = None; // (monotonic, wall-clock us)
    let mut buf = vec![0u8; 65536];
    let mut events = mio::Events::with_capacity(1024);

    loop {
        let (conn_idx, established) = {
            let st = st.borrow();
            (st.conn, st.established)
        };
        let mut wait = None;
        if let (Some(idx), Some(est)) = (conn_idx, established) {
            if start.is_none() && Instant::now() >= est + warmup {
                start = Some((Instant::now(), now_us()));
                info!("streaming {} frames", frames.len());
            }
            match start {
                Some((t0, w0)) => {
                    // Write every frame that is due.
                    while next < frames.len() && Instant::now() >= t0 + Duration::from_micros(frames[next].pts_us) {
                        let f = &frames[next];
                        let Some(conn) = endpoint.conn_get_mut(idx) else { break };
                        let sid = conn.stream_uni_new(0, false)?;
                        let ts = now_us();
                        let mut data = vec![0u8; f.size];
                        data[..HEADER_LEN].copy_from_slice(&encode_header(f.id, f.size, ts));
                        let data = Bytes::from(data);
                        let n = conn.stream_write(sid, data.clone(), true)?;
                        let due = t0 + Duration::from_micros(f.pts_us) + Duration::from_millis(opt.deadline_ms);
                        conn.stream_set_deadline(sid, due);
                        if n < data.len() {
                            st.borrow_mut().pending.insert(sid, data.slice(n..));
                            conn.stream_want_write(sid, true)?;
                        }
                        writeln!(out, "{},{},{},{},{},{}", f.id, f.typ, f.size, sid, w0 + f.pts_us, ts)?;
                        next += 1;
                    }
                    if next < frames.len() {
                        wait = Some((t0 + Duration::from_micros(frames[next].pts_us)).saturating_duration_since(Instant::now()));
                    }
                }
                None => wait = Some((est + warmup).saturating_duration_since(Instant::now())),
            }
        }

        endpoint.process_connections()?;
        if term.load(Ordering::Relaxed) || st.borrow().closed {
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
    log::logger().flush();
    Ok(())
}
