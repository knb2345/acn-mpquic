# Learned bandwidth predictor

The core of the project. The `learned` scheduler decides, frame by frame,
which path(s) carry a video frame from each path's predicted finish time
`T_p = (Q_p + S) / B_p + sRTT_p / 2`. Here `B_p` is not a fixed rule: a small
neural network predicts a low quantile of the path's capacity over the next
0.5 s from the last second of what the sender observes on that path.

## Model

- Input (44 values, `features.py` = `ml_features.rs`): per 100 ms tick over
  the last second, the delivery rate, send rate (log kB/s), queueing delay
  (sRTT minus min RTT) and loss ratio; plus the congestion controller's
  bandwidth estimate, cwnd/sRTT, inflight/cwnd and min RTT now.
- Output: the 10th, 20th, 30th and 50th percentile of the mean capacity over
  the next 0.5 s (ln(1 + kB/s)). The scheduler uses one of them (`USE_Q`);
  a low quantile is a cautious estimate, which replaces the hand-tuned
  85% headroom and 3 s rate memory of the `deadline` scheduler.
- Network: 44 -> 32 -> 32 -> 4, ReLU, trained with the pinball (quantile)
  loss and Adam in numpy (`train.py`); exported as Rust constants into
  `third_party/tquic/src/multipath_scheduler/ml_model.rs` and evaluated on the
  send path every 100 ms per path (two small matrix products).

## Data: training never sees the test traces

`testbed/collect.sh` streams the lecture under conditions drawn by
`testbed/train_spec.py` from six training traces (`traces/train/`: Verizon
LTE x2, T-Mobile LTE x2, AT&T LTE 2016, Verizon EVDO), scaled to 1-6 Mbit/s,
plus constant rates and random outages. The test scenarios use AT&T LTE
driving and T-Mobile 3G (`traces/`), which are never used for training.
Labels are exact: the replay log says what each path's capacity was.

## Commands

Collect (after `testbed/freeze_bins.sh <tag>`):

```bash
BIN_DIR=.cache/bin/ml0 testbed/collect.sh 1 8 minrtt roundrobin deadline
```

Build the dataset and train (writes `ml_model.rs`; rebuild TQUIC and the
streamer afterwards):

```bash
python3 analysis/ml/features.py results/ml_train/train.npz results/ml_train/*/*/*
```

```bash
python3 analysis/ml/train.py results/ml_train/train.npz --use-q 0.2
```

Evaluate the predictor offline on held-out test runs (any run of
`testbed/run_stream.sh` logs `ml_features.csv`), against BBR's estimate, the
3 s peak heuristic and recent delivery rates:

```bash
python3 analysis/ml/features.py results/ml_test.npz results/copyrightx_lecture/*/*/*
```

```bash
python3 analysis/ml/eval_predictor.py results/ml_train/train.model.npz results/ml_test.npz --by scenario
```

Check that the deployed Rust model predicts what the Python model does:

```bash
python3 analysis/ml/eval_predictor.py --parity results/ml_train/train.model.npz results/copyrightx_lecture/trace/learned/1
```
