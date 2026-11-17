//! Shared pieces of the frame sender and receiver.

use std::collections::VecDeque;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::sync::Mutex;

use log::{Level, LevelFilter, Metadata, Record};

pub const ALPN: &[u8] = b"mpq-video";
pub const HEADER_LEN: usize = 16;
pub const FRAME_PATH_TARGET: &str = "tquic::frame_path";

/// CLOCK_MONOTONIC in microseconds.
pub fn now_us() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000 + ts.tv_nsec as u64 / 1000
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum Policy {
    /// Standard TQUIC multipath scheduler (S0-S4, R1).
    Tquic,
    /// Whole frame placed on path with earliest predicted finish time.
    F1,
    /// F1 + balanced split + 1xPTO rescue on alternate path.
    F2,
    /// F2 logic over two independent single-path QUIC connections.
    A1,
    /// F2 using ground-truth trace rates instead of online estimates.
    R2,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Frame {
    pub id: u32,
    pub typ: char,
    pub size: usize,
    pub pts_us: u64,
}

pub fn load_frames(path: &Path) -> std::io::Result<Vec<Frame>> {
    parse_frames(&std::fs::read_to_string(path)?)
}

pub fn parse_frames(text: &str) -> std::io::Result<Vec<Frame>> {
    let mut frames = Vec::new();
    for line in text.lines().skip(1).filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split(',').collect();
        let bad = || std::io::Error::new(std::io::ErrorKind::InvalidData, line.to_string());
        if f.len() < 4 {
            return Err(bad());
        }
        frames.push(Frame {
            id: f[0].parse().map_err(|_| bad())?,
            typ: f[1].chars().next().ok_or_else(bad)?,
            size: f[2].parse().map_err(|_| bad())?,
            pts_us: (f[3].parse::<f64>().map_err(|_| bad())? * 1000.0).round() as u64,
        });
    }
    Ok(frames)
}

pub fn rotate(frames: &[Frame], offset: usize) -> Vec<(Frame, u64)> {
    let n = frames.len();
    if n == 0 {
        return Vec::new();
    }
    let interval = if n > 1 { (frames[n - 1].pts_us - frames[0].pts_us) / (n as u64 - 1) } else { 0 };
    let span = frames[n - 1].pts_us - frames[0].pts_us + interval;
    let base = frames[offset % n].pts_us;
    (0..n)
        .map(|i| {
            let k = (offset + i) % n;
            let t = if k >= offset % n { frames[k].pts_us - base } else { frames[k].pts_us + span - base };
            (frames[k].clone(), t)
        })
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PartHeader {
    pub frame_id: u32,
    pub offset: u32,
    pub length: u32,
    pub frame_size: u32,
}

impl PartHeader {
    pub fn encode(&self) -> [u8; HEADER_LEN] {
        let mut h = [0u8; HEADER_LEN];
        h[0..4].copy_from_slice(&self.frame_id.to_be_bytes());
        h[4..8].copy_from_slice(&self.offset.to_be_bytes());
        h[8..12].copy_from_slice(&self.length.to_be_bytes());
        h[12..16].copy_from_slice(&self.frame_size.to_be_bytes());
        h
    }

    pub fn decode(h: &[u8]) -> PartHeader {
        let u = |i: usize| u32::from_be_bytes(h[i..i + 4].try_into().unwrap());
        PartHeader { frame_id: u(0), offset: u(4), length: u(8), frame_size: u(12) }
    }
}

#[derive(Clone, Debug, Default)]
pub struct Coverage {
    ranges: Vec<(u32, u32)>,
}

impl Coverage {
    pub fn add(&mut self, start: u32, end: u32) {
        if start >= end {
            return;
        }
        let (mut s, mut e) = (start, end);
        let mut out = Vec::with_capacity(self.ranges.len() + 1);
        for &(a, b) in &self.ranges {
            if b < s || a > e {
                out.push((a, b));
            } else {
                s = s.min(a);
                e = e.max(b);
            }
        }
        out.push((s, e));
        out.sort_unstable();
        self.ranges = out;
    }

    pub fn complete(&self, size: u32) -> bool {
        size == 0 || self.ranges.first().is_some_and(|&(a, b)| a == 0 && b >= size)
    }

    pub fn covered_bytes(&self) -> u64 {
        self.ranges.iter().map(|&(a, b)| (b - a) as u64).sum()
    }
}

/// Measured delivery rate R_p: max bytes acknowledged per interval over the last 1 s.
#[derive(Clone, Debug)]
pub struct RateEstimator {
    last_ts_us: Option<u64>,
    last_acked: u64,
    /// (timestamp_us, bytes_per_sec)
    samples: VecDeque<(u64, f64)>,
    fallback_bps: f64,
}

impl RateEstimator {
    pub fn new(initial_kbit: u64) -> Self {
        Self {
            last_ts_us: None,
            last_acked: 0,
            samples: VecDeque::new(),
            // Default to 1 Mbps if unknown until first ACK arrives
            fallback_bps: (initial_kbit.max(100) as f64) * 1000.0 / 8.0,
        }
    }

    pub fn update(&mut self, ts_us: u64, acked_bytes: u64) {
        if let Some(prev_ts) = self.last_ts_us {
            let dt_us = ts_us.saturating_sub(prev_ts);
            if dt_us >= 5_000 && acked_bytes > self.last_acked {
                let d_bytes = (acked_bytes - self.last_acked) as f64;
                let rate_bps = d_bytes * 1_000_000.0 / (dt_us as f64);
                self.samples.push_back((ts_us, rate_bps));
                self.last_ts_us = Some(ts_us);
                self.last_acked = acked_bytes;
            }
        } else {
            self.last_ts_us = Some(ts_us);
            self.last_acked = acked_bytes;
        }
        while let Some(&(t, _)) = self.samples.front() {
            if ts_us.saturating_sub(t) > 1_000_000 {
                self.samples.pop_front();
            } else {
                break;
            }
        }
    }

    /// Delivery rate R_p in bytes/second.
    pub fn rate_bytes_per_sec(&self) -> f64 {
        self.samples
            .iter()
            .map(|&(_, r)| r)
            .fold(0.0_f64, f64::max)
            .max(if self.samples.is_empty() { self.fallback_bps } else { 10_000.0 })
    }
}

#[derive(Clone, Copy, Debug)]
pub struct PathEstimate {
    /// Bytes assigned to path p not yet sent (U_p).
    pub queued_bytes: f64,
    /// Measured delivery rate R_p in bytes/sec.
    pub rate_bps: f64,
    /// Smoothed RTT in microseconds.
    pub srtt_us: f64,
    /// Minimum observed RTT in microseconds.
    pub min_rtt_us: f64,
}

impl PathEstimate {
    /// Base one-way + queueing delay d_p = (srtt_p - min_rtt_p) + min_rtt_p / 2 (in us).
    pub fn base_delay_us(&self) -> f64 {
        let q_delay = (self.srtt_us - self.min_rtt_us).max(0.0);
        q_delay + 0.5 * self.min_rtt_us
    }

    /// Predicted finish time T_p (in us) for S bytes on path p:
    /// T_p = (U_p + S) / R_p + (srtt_p - min_rtt_p) + min_rtt_p / 2
    pub fn finish_time_us(&self, s_bytes: f64) -> f64 {
        let tx_us = ((self.queued_bytes + s_bytes) / self.rate_bps.max(1.0)) * 1_000_000.0;
        tx_us + self.base_delay_us()
    }
}

/// F2 balanced split: returns Some(s_a) in (0, s_total) iff splitting lowers
/// max(T_a(S_a), T_b(S - S_a)) below sending the whole frame on either single path.
pub fn balanced_split_bytes(a: PathEstimate, b: PathEstimate, s_total: usize) -> Option<usize> {
    if s_total < 256 {
        return None;
    }
    let s = s_total as f64;
    let whole_a = a.finish_time_us(s);
    let whole_b = b.finish_time_us(s);
    let best_single = whole_a.min(whole_b);

    let inv_ra = 1_000_000.0 / a.rate_bps.max(1.0); // us per byte
    let inv_rb = 1_000_000.0 / b.rate_bps.max(1.0); // us per byte
    let num = (b.queued_bytes + s) * inv_rb + b.base_delay_us()
        - a.queued_bytes * inv_ra
        - a.base_delay_us();
    let s_a_raw = num / (inv_ra + inv_rb);
    let s_a = s_a_raw.round().clamp(0.0, s) as usize;
    let s_b = s_total - s_a;

    if s_a == 0 || s_b == 0 {
        return None;
    }
    let split_finish = a.finish_time_us(s_a as f64).max(b.finish_time_us(s_b as f64));
    if split_finish + 100.0 < best_single {
        Some(s_a)
    } else {
        None
    }
}

/// Ground-truth rate oracle for R2 (reads static kbit/s + optional replay schedule).
pub struct TrueRateOracle {
    default_a_bps: f64,
    default_b_bps: f64,
    /// (rel_ms, a_kbit, b_kbit)
    schedule: Vec<(u64, u64, u64)>,
}

impl TrueRateOracle {
    pub fn new(a_kbit: u64, b_kbit: u64, replay_csv: Option<&Path>) -> Self {
        let mut schedule = Vec::new();
        if let Some(p) = replay_csv {
            if let Ok(text) = std::fs::read_to_string(p) {
                for line in text.lines().skip(1).filter(|l| !l.is_empty()) {
                    let cols: Vec<&str> = line.split(',').collect();
                    if cols.len() >= 3 {
                        if let (Ok(t), Ok(ka), Ok(kb)) = (
                            cols[0].parse::<u64>(),
                            cols[1].parse::<u64>(),
                            cols[2].parse::<u64>(),
                        ) {
                            schedule.push((t, ka, kb));
                        }
                    }
                }
            }
        }
        Self {
            default_a_bps: (a_kbit.max(1) as f64) * 1000.0 / 8.0,
            default_b_bps: (b_kbit.max(1) as f64) * 1000.0 / 8.0,
            schedule,
        }
    }

    pub fn rates_at_ms(&self, elapsed_ms: u64) -> (f64, f64) {
        if self.schedule.is_empty() {
            return (self.default_a_bps, self.default_b_bps);
        }
        let idx = self.schedule.partition_point(|&(t, _, _)| t <= elapsed_ms).saturating_sub(1);
        let (_, ka, kb) = self.schedule[idx];
        ((ka.max(1) as f64) * 1000.0 / 8.0, (kb.max(1) as f64) * 1000.0 / 8.0)
    }
}

pub struct Route {
    pub target: &'static str,
    pub path: std::path::PathBuf,
    pub header: Option<&'static str>,
    pub sep: char,
}

impl Route {
    pub fn csv(target: &'static str, path: &Path, header: &'static str) -> Route {
        Route { target, path: path.to_path_buf(), header: Some(header), sep: ',' }
    }
}

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
        if self.route(m.target()).is_some() {
            return m.level() <= Level::Debug;
        }
        if m.target() == FRAME_PATH_TARGET {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finish_time_hand_computed() {
        // U_p = 2000 B, S = 3000 B, R_p = 100_000 B/s -> tx = 50_000 us
        // srtt = 50_000 us, min_rtt = 30_000 us -> q_delay = 20_000, one_way = 15_000 -> base = 35_000 us
        // Total T_p = 85_000 us
        let est = PathEstimate {
            queued_bytes: 2000.0,
            rate_bps: 100_000.0,
            srtt_us: 50_000.0,
            min_rtt_us: 30_000.0,
        };
        assert!((est.finish_time_us(3000.0) - 85_000.0).abs() < 1e-6);
    }

    #[test]
    fn balanced_split_hand_computed() {
        // Equal rates 200_000 B/s (5 us/B), U_a = U_b = 0, base_a = 15_000 us, base_b = 25_000 us
        // Frame S = 10_000 B. Single A = 50_000 + 15_000 = 65_000 us; Single B = 75_000 us.
        // S_a = (10_000 * 5 + 25_000 - 15_000) / 10 = 6_000 B -> T_a = 45_000 us, T_b = 45_000 us.
        let a = PathEstimate { queued_bytes: 0.0, rate_bps: 200_000.0, srtt_us: 30_000.0, min_rtt_us: 30_000.0 };
        let b = PathEstimate { queued_bytes: 0.0, rate_bps: 200_000.0, srtt_us: 50_000.0, min_rtt_us: 50_000.0 };
        assert_eq!(balanced_split_bytes(a, b, 10_000), Some(6_000));

        // Tiny frame S = 500 B: Single A = 2_500 + 15_000 = 17_500 us < base_b (25_000 us) -> no split.
        assert_eq!(balanced_split_bytes(a, b, 500), None);
    }

    #[test]
    fn rate_estimator_1s_window() {
        let mut r = RateEstimator::new(800);
        r.update(1_000_000, 0);
        r.update(1_100_000, 20_000); // 200_000 B/s
        r.update(1_200_000, 30_000); // 100_000 B/s
        assert!((r.rate_bytes_per_sec() - 200_000.0).abs() < 1e-3);
        // Advance past 1s window from first sample -> 200k sample expires, 100k remains
        r.update(2_150_000, 35_000); // 5_000 / 0.95s ~ 5263 B/s -> max in window is 100_000 B/s
        assert!((r.rate_bytes_per_sec() - 100_000.0).abs() < 1e-3);
    }

    #[test]
    fn header_and_coverage_tests() {
        let h = PartHeader { frame_id: 7, offset: 1000, length: 2345, frame_size: 55_000 };
        assert_eq!(PartHeader::decode(&h.encode()), h);
        let mut c = Coverage::default();
        c.add(60, 100);
        assert!(!c.complete(100));
        c.add(0, 60);
        assert!(c.complete(100));
    }

    // Reassembly and clip-rotation tests from the original streamer (restored at merge).
    #[test]
    fn coverage_whole_frame_in_one_part() {
        let mut c = Coverage::default();
        assert!(!c.complete(100));
        c.add(0, 100);
        assert!(c.complete(100));
    }

    #[test]
    fn coverage_split_parts_any_order() {
        // Two parts of a 100-byte frame, second part first.
        let mut c = Coverage::default();
        c.add(60, 100);
        assert!(!c.complete(100));
        c.add(0, 60);
        assert!(c.complete(100));
        assert_eq!(c.covered_bytes(), 100);
    }

    #[test]
    fn coverage_gap_and_rescue_overlap() {
        // Parts [0,40) and [70,100) arrived; the rescue resends [30,80) on the
        // other path, overlapping both: complete, 100 distinct bytes.
        let mut c = Coverage::default();
        c.add(0, 40);
        c.add(70, 100);
        assert!(!c.complete(100));
        assert_eq!(c.covered_bytes(), 70);
        c.add(30, 80);
        assert!(c.complete(100));
        assert_eq!(c.covered_bytes(), 100);
    }

    #[test]
    fn coverage_duplicate_and_adjacent() {
        let mut c = Coverage::default();
        c.add(0, 50);
        c.add(0, 50); // duplicate (redundant scheduler, rescue)
        c.add(50, 99); // adjacent, one byte short
        assert!(!c.complete(100));
        assert_eq!(c.covered_bytes(), 99);
        c.add(99, 100);
        assert!(c.complete(100));
    }

    #[test]
    fn parse_and_rotate() {
        let text = "frame_id,frame_type,size_bytes,pts_ms\n0,I,500,0\n1,P,100,40\n2,P,120,80\n3,P,90,120\n";
        let f = parse_frames(text).unwrap();
        assert_eq!(f.len(), 4);
        assert_eq!(f[2], Frame { id: 2, typ: 'P', size: 120, pts_us: 80_000 });
        let r = rotate(&f, 2);
        let got: Vec<(u32, u64)> = r.iter().map(|(fr, t)| (fr.id, *t)).collect();
        // Frames 2, 3, then 0, 1 one interval after frame 3.
        assert_eq!(got, vec![(2, 0), (3, 40_000), (0, 80_000), (1, 120_000)]);
        let r0 = rotate(&f, 0);
        assert_eq!(r0.iter().map(|(_, t)| *t).collect::<Vec<_>>(), vec![0, 40_000, 80_000, 120_000]);
    }
}
