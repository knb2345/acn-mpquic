//! Shared pieces of the frame sender and receiver.
//!
//! Each video frame travels on its own unidirectional QUIC stream from the
//! sender (QUIC server) to the receiver (QUIC client). The stream carries a
//! 16-byte header (frame id, frame size, send time) padded to the frame's
//! encoded size from the workload trace.

use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::sync::Mutex;

use log::{Level, LevelFilter, Metadata, Record};

pub const ALPN: &[u8] = b"mpq-video";
pub const HEADER_LEN: usize = 16;
/// Log target of the per-path accounting hook in TQUIC (team7 branch).
pub const FRAME_PATH_TARGET: &str = "tquic::frame_path";
/// Log target of the deadline scheduler's per-frame plans (team7 branch).
pub const PLAN_TARGET: &str = "tquic::deadline_plan";

/// CLOCK_MONOTONIC in microseconds. Sender and receiver run on the same host
/// (two network namespaces, one kernel clock), so their timestamps compare
/// directly. Not the wall clock: WSL2 steps it by about half a second every
/// half minute or so, which shows up as fake frame lateness.
pub fn now_us() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: valid pointer to a timespec; CLOCK_MONOTONIC always exists on Linux.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000 + ts.tv_nsec as u64 / 1000
}

#[derive(Clone, Debug)]
pub struct Frame {
    pub id: u32,
    pub typ: char,
    pub size: usize,
    pub pts_us: u64,
}

/// Read a workload trace (csv: frame_id,frame_type,size_bytes,pts_ms).
pub fn load_frames(path: &Path) -> std::io::Result<Vec<Frame>> {
    let text = std::fs::read_to_string(path)?;
    let mut frames = Vec::new();
    for line in text.lines().skip(1).filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split(',').collect();
        let bad = || std::io::Error::new(std::io::ErrorKind::InvalidData, line.to_string());
        frames.push(Frame {
            id: f[0].parse().map_err(|_| bad())?,
            typ: f[1].chars().next().ok_or_else(bad)?,
            // A frame must at least hold the header.
            size: f[2].parse::<usize>().map_err(|_| bad())?.max(HEADER_LEN),
            pts_us: (f[3].parse::<f64>().map_err(|_| bad())? * 1000.0).round() as u64,
        });
    }
    Ok(frames)
}

pub fn encode_header(frame_id: u32, size: usize, send_ts_us: u64) -> [u8; HEADER_LEN] {
    let mut h = [0u8; HEADER_LEN];
    h[0..4].copy_from_slice(&frame_id.to_be_bytes());
    h[4..8].copy_from_slice(&(size as u32).to_be_bytes());
    h[8..16].copy_from_slice(&send_ts_us.to_be_bytes());
    h
}

/// (frame_id, size, send_ts_us)
pub fn decode_header(h: &[u8]) -> (u32, usize, u64) {
    (
        u32::from_be_bytes(h[0..4].try_into().unwrap()),
        u32::from_be_bytes(h[4..8].try_into().unwrap()) as usize,
        u64::from_be_bytes(h[8..16].try_into().unwrap()),
    )
}

/// Log targets of TQUIC's team7 hooks that the streamer can route to files.
pub const ML_FEATURES_TARGET: &str = "tquic::ml_features";
pub const ML_PRED_TARGET: &str = "tquic::ml_pred";

/// Where records of one TQUIC log target go: their own file, each line
/// prefixed with the monotonic time ("<ts_us><sep><message>").
pub struct Route {
    pub target: &'static str,
    pub path: std::path::PathBuf,
    /// First line of the file, if any (a csv header).
    pub header: Option<&'static str>,
    pub sep: char,
}

impl Route {
    pub fn csv(target: &'static str, path: &Path, header: &'static str) -> Route {
        Route { target, path: path.to_path_buf(), header: Some(header), sep: ',' }
    }
}

/// Logger that writes ordinary log records to one file and the records of
/// each routed target (per-frame path bytes, scheduler plans, ML samples and
/// predictions) to their own files, so analysis needs no log parsing.
pub struct SplitLogger {
    level: LevelFilter,
    main: Mutex<BufWriter<File>>,
    routes: Vec<(&'static str, char, Mutex<BufWriter<File>>)>,
}

impl SplitLogger {
    pub fn install(level: LevelFilter, main: &Path, routes: Vec<Route>) -> std::io::Result<()> {
        let mut open = Vec::new();
        for r in routes {
            let mut w = BufWriter::new(File::create(&r.path)?);
            if let Some(h) = r.header {
                writeln!(w, "{h}")?;
            }
            open.push((r.target, r.sep, Mutex::new(w)));
        }
        let max = if open.is_empty() { level } else { level.max(LevelFilter::Debug) };
        let logger = SplitLogger { level, main: Mutex::new(BufWriter::new(File::create(main)?)), routes: open };
        log::set_boxed_logger(Box::new(logger)).expect("logger already set");
        log::set_max_level(max);
        Ok(())
    }

    fn route(&self, target: &str) -> Option<&(&'static str, char, Mutex<BufWriter<File>>)> {
        self.routes.iter().find(|(t, _, _)| *t == target)
    }
}

impl log::Log for SplitLogger {
    fn enabled(&self, m: &Metadata) -> bool {
        if m.target().starts_with("tquic::") && self.route(m.target()).is_some() {
            return m.level() <= Level::Debug;
        }
        // Routed-only targets stay out of the main log.
        if [FRAME_PATH_TARGET, PLAN_TARGET, ML_FEATURES_TARGET, ML_PRED_TARGET].contains(&m.target()) {
            return false;
        }
        m.level() <= self.level
    }

    fn log(&self, r: &Record) {
        if !self.enabled(r.metadata()) {
            return;
        }
        match self.route(r.target()) {
            Some((_, sep, w)) => {
                let _ = writeln!(w.lock().unwrap(), "{}{}{}", now_us(), sep, r.args());
            }
            None => {
                let _ = writeln!(self.main.lock().unwrap(), "{} {} {}: {}", now_us(), r.level(), r.target(), r.args());
            }
        }
    }

    fn flush(&self) {
        let _ = self.main.lock().unwrap().flush();
        for (_, _, w) in &self.routes {
            let _ = w.lock().unwrap().flush();
        }
    }
}
