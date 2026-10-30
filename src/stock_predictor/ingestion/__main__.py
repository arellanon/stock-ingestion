from stock_predictor.ingestion.cli import app

def main():
    try: app()
    except Exception as exc:
        # Redact sensitive SDK/DB exception details in production CLI.
        import sys
        print(f"Error ({type(exc).__name__}). Check configuration, database, provider and permissions.",file=sys.stderr)
        raise SystemExit(1)
if __name__ == "__main__": main()
