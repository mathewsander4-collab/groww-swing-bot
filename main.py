"""
NIFTY 500 Swing Trading Bot — Groww Cloud API

Usage:
    python main.py auth                       # login with API Key + Secret
    python main.py auth --totp                # login with TOTP (automated, recommended)
    python main.py universe --refresh         # build NIFTY 500 symbol list
    python main.py scan                       # today's setups (run after 3:30 PM)
    python main.py backtest --years 3         # backtest strategies
"""
import argparse


def main():
    parser = argparse.ArgumentParser(description="NIFTY 500 swing bot (Groww Cloud)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="Login and save access token")
    p_auth.add_argument("--totp", action="store_true", help="Use TOTP flow (no daily approval needed)")

    p_uni = sub.add_parser("universe", help="Build/refresh NIFTY 500 symbol list")
    p_uni.add_argument("--refresh", action="store_true")

    p_scan = sub.add_parser("scan", help="Run today's scanner (after 3:30 PM)")
    p_scan.add_argument("--refresh-universe", action="store_true")

    p_bt = sub.add_parser("backtest", help="Backtest the strategies")
    p_bt.add_argument("--years", type=int, default=3)
    p_bt.add_argument("--refresh-data", action="store_true")
    p_bt.add_argument("--refresh-universe", action="store_true")

    args = parser.parse_args()

    if args.command == "auth":
        from groww_auth import (get_token_via_key_secret, get_token_via_totp,
                                save_token)
        token = get_token_via_totp() if args.totp else get_token_via_key_secret()
        save_token(token)

    elif args.command == "universe":
        from universe import build_universe
        u = build_universe(force_refresh=args.refresh)
        print(f"Universe ready: {len(u)} symbols.")

    elif args.command == "scan":
        from scanner import run_scan, print_report
        import config
        from datetime import datetime
        results = run_scan(refresh_universe=args.refresh_universe)
        print_report(results)
        if not results.empty:
            out_path = f"{config.DATA_DIR}/scan_{datetime.now().strftime('%Y%m%d')}.csv"
            results.to_csv(out_path, index=False)
            print(f"\nSaved to {out_path}")

    elif args.command == "backtest":
        from universe import build_universe
        from backtest import load_all_histories, run_backtest, compute_metrics
        import config, os
        uni = build_universe(force_refresh=args.refresh_universe)
        histories = load_all_histories(uni, args.years, args.refresh_data)
        result = run_backtest(histories, config)
        metrics = compute_metrics(result, config)
        print("\nBACKTEST RESULTS")
        print("=" * 50)
        for k, v in metrics.items():
            print(f"{k:25s}: {v}")
        trades_path = os.path.join(config.DATA_DIR, "backtest_trades.csv")
        result["trades"].to_csv(trades_path, index=False)
        print(f"\nTrade log saved to {trades_path}")


if __name__ == "__main__":
    main()
