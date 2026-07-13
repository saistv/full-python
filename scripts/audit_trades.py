"""Apply the strategy-audit standards to a trades.csv (or two, for A/B).

Reports the things a raw perturb table omits: median alongside mean,
outlier sensitivity (remove top 1-3 winners), and a significance test.
For an A/B it runs Welch's t on the per-trade P&L of the two populations
AND flags the audit's core trap: does the "better" variant win on net
P&L, or only on PF while blocking positive-EV trades?

Usage:
  python3 scripts/audit_trades.py baseline_trades.csv [variant_trades.csv]
"""
import csv
import math
import statistics
import sys


def load(path):
    return [float(r["net_pnl"]) for r in csv.DictReader(open(path))]


def summ(p):
    wins = [x for x in p if x > 0]
    losses = [x for x in p if x <= 0]
    pf = sum(wins) / -sum(losses) if losses and sum(losses) != 0 else float("inf")
    mean = sum(p) / len(p)
    # one-sample t vs zero (is per-trade expectancy > 0?)
    sd = statistics.pstdev(p) if len(p) > 1 else 0.0
    t0 = mean / (sd / math.sqrt(len(p))) if sd > 0 else 0.0
    return dict(n=len(p), net=sum(p), wr=len(wins) / len(p), pf=pf,
               mean=mean, median=statistics.median(p), t0=t0,
               best=max(p), worst=min(p))


def outlier_sens(p, name):
    s = sorted(p, reverse=True)
    print(f"  outlier sensitivity ({name}):")
    for k in (1, 2, 3):
        removed = sum(s[:k])
        print(f"    remove top {k}: net {sum(s[k:]):+,.0f}  "
              f"(dropped {removed:+,.0f}, {removed/sum(p)*100:.0f}% of net)")


def welch(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va, vb = statistics.pvariance(a), statistics.pvariance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (mb - ma) / se if se > 0 else 0.0


def show(name, p):
    s = summ(p)
    print(f"\n{name}: n={s['n']} net={s['net']:+,.0f} WR={s['wr']:.1%} "
          f"PF={s['pf']:.3f}")
    print(f"  mean={s['mean']:+.0f} median={s['median']:+.0f} "
          f"t(vs0)={s['t0']:+.2f} best={s['best']:+,.0f} worst={s['worst']:+,.0f}")
    outlier_sens(p, name)
    return s


base = load(sys.argv[1])
sb = show(f"BASELINE ({sys.argv[1].split('/')[-2]})", base)

if len(sys.argv) > 2:
    var = load(sys.argv[2])
    sv = show(f"VARIANT ({sys.argv[2].split('/')[-2]})", var)
    print("\n--- A/B verdict (audit standards) ---")
    print(f"  net delta:  {sv['net']-sb['net']:+,.0f}")
    print(f"  PF delta:   {sv['pf']-sb['pf']:+.3f}")
    print(f"  trade delta:{sv['n']-sb['n']:+d}  "
          f"(if PF up but net down / trades blocked => Standard 7 trap)")
    print(f"  Welch t (per-trade P&L, variant vs baseline): {welch(base,var):+.2f}")
    print("  NOTE: |t|<2.0 = not significant. Promotion requires every")
    print("        pre-registered Gate 1 row against the Python control.")
