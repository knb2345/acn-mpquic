//! Frame receiver (QUIC client). Connects from the first local address, adds
//! one path per further local address, and records when each frame's stream
//! is complete. Closes the connection once every frame of the workload has
//! arrived or --max-duration-s has passed.
//!
//! Output (--out): frame_id,stream_id,size_bytes,send_ts_us,first_byte_ts_us,complete_ts_us

use std::cell::RefCell;
use std::collections::HashMap;
use std::error::Error;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::net::{IpAddr, SocketAddr};
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
#[clap(about = "Receive a frame stream over multipath TQUIC and log frame completion times")]
struct Opt {
    #[clap(long)]
    connect: SocketAddr,
    /// One QUIC path per address; the first one carries the handshake.
    #[clap(long, value_delimiter = ',', required = true)]
    local_addresses: Vec<IpAddr>,
    #[clap(long)]
    enable_multipath: bool,
    #[clap(long, default_value = "minrtt")]
    multipath_algor: MultipathAlgorithm,
    #[clap(long, default_value = "bbr")]
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
}

/// A frame stream being received.
struct Rx {
    header: Vec<u8>,
    first_byte_ts: u64,
}

#[derive(Default)]
struct State {
    conn: Option<u64>,
    closed: bool,
    extra_paths: Vec<SocketAddr>,
    remote: Option<SocketAddr>,
    rx: HashMap<u64, Rx>,
    rows: Vec<String>,
    qlog_dir: Option<PathBuf>,
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
        st.conn = conn.index();
        info!("{} established", conn.trace_id());
        let remote = st.remote.unwrap();
        for local in st.extra_paths.clone() {
            match conn.add_path(local, remote) {
                Ok(_) => info!("added path {local} -> {remote}"),
                Err(e) => error!("add_path {local}: {e:?}"),
            }
        }
    }

    fn on_conn_closed(&mut self, conn: &mut Connection) {
        info!("{} closed", conn.trace_id());
        self.st.borrow_mut().closed = true;
    }

    fn on_stream_created(&mut self, _conn: &mut Connection, _stream_id: u64) {}

    fn on_stream_readable(&mut self, conn: &mut Connection, stream_id: u64) {
        let mut st = self.st.borrow_mut();
        loop {
            match conn.stream_read(stream_id, &mut self.buf) {
                Ok((n, fin)) => {
                    let rx = st
                        .rx
                        .entry(stream_id)
                        .or_insert_with(|| Rx { header: Vec::with_capacity(HEADER_LEN), first_byte_ts: now_us() });
                    let need = HEADER_LEN - rx.header.len();
                    rx.header.extend_from_slice(&self.buf[..n.min(need)]);
                    if fin {
                        let done = now_us();
                        let rx = st.rx.remove(&stream_id).unwrap();
                        if rx.header.len() < HEADER_LEN {
                            warn!("stream {stream_id} ended without a full header");
                            break;
                        }
                        let (id, size, send_ts) = decode_header(&rx.header);
                        st.rows.push(format!("{id},{stream_id},{size},{send_ts},{},{done}", rx.first_byte_ts));
                        break;
                    }
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
    let mut tls = TlsConfig::new_client_config(vec![ALPN.to_vec()], false)?;
    tls.set_verify(false);
    config.set_tls_config(tls);

    let mut poll = mio::Poll::new()?;
    let mut sock = QuicSocket::new(&SocketAddr::new(opt.local_addresses[0], 0), poll.registry())?;
    let mut extra_paths = Vec::new();
    if opt.enable_multipath {
        for ip in &opt.local_addresses[1..] {
            extra_paths.push(sock.add(&SocketAddr::new(*ip, 0), poll.registry())?);
        }
    }
    let sock = Rc::new(sock);
    let st = Rc::new(RefCell::new(State {
        extra_paths,
        remote: Some(opt.connect),
        qlog_dir: opt.qlog_dir.clone(),
        ..Default::default()
    }));
    let handler = Handler { st: st.clone(), buf: vec![0u8; 65536] };
    let mut endpoint = Endpoint::new(Box::new(config), false, Box::new(handler), sock.clone());
    endpoint.connect(sock.local_addr(), opt.connect, Some("example.org"), None, None, None)?;
    info!("connecting {} -> {}", sock.local_addr(), opt.connect);

    let term = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, term.clone())?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, term.clone())?;

    let started = Instant::now();
    let deadline = started + Duration::from_secs(opt.max_duration_s);
    let mut closing_since: Option<Instant> = None;
    let mut buf = vec![0u8; 65536];
    let mut events = mio::Events::with_capacity(1024);

    loop {
        endpoint.process_connections()?;
        let (done, closed, conn) = {
            let st = st.borrow();
            (st.rows.len() >= expected, st.closed, st.conn)
        };
        if closed || closing_since.is_some_and(|t| t.elapsed() > Duration::from_secs(2)) {
            break;
        }
        if closing_since.is_none() && (done || Instant::now() >= deadline || term.load(Ordering::Relaxed)) {
            info!("closing: {} of {expected} frames complete", st.borrow().rows.len());
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
    writeln!(out, "frame_id,stream_id,size_bytes,send_ts_us,first_byte_ts_us,complete_ts_us")?;
    for r in &st.rows {
        writeln!(out, "{r}")?;
    }
    out.flush()?;
    info!("received {} of {expected} frames in {:?}", st.rows.len(), started.elapsed());
    log::logger().flush();
    Ok(())
}
