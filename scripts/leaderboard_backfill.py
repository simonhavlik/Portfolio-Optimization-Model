"""Load EDGAR filings and rebuild the leaderboard in the configured environment."""

import argparse
import json
from pathlib import Path

from flask import Flask

from config import config
from models.database import AppCache, FundFiling, db
from services.fund_archive import import_archive, publish_quarters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drafts', type=Path, help='Import a private JSON backlog into the database')
    parser.add_argument('--map-limit', type=int, default=0, help='Resolve up to this many CUSIPs on OpenFIGI')
    parser.add_argument('--price-limit', type=int, default=0, help='Fetch up to this many missing adjusted histories')
    parser.add_argument('--publish-saved', action='store_true', help='Persist existing rankings, market history and chart assets')
    parser.add_argument('--import-only', action='store_true', help='Skip rebuilding the ranking and chart exports')
    args = parser.parse_args()
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI
    db.init_app(app)
    with app.app_context():
        FundFiling.__table__.create(db.engine, checkfirst=True)
        print('Filings by manager:', import_archive(), flush=True)
        publish_quarters()
        from services.leaderboard import BOARDS, build_board
        if args.map_limit or args.price_limit:
            from services.leaderboard_market import refresh_mapping, refresh_missing_prices
            if args.map_limit:
                print('Known CUSIPs:', len(refresh_mapping(args.map_limit)), flush=True)
            if args.price_limit:
                print('Price series added:', refresh_missing_prices(args.price_limit), flush=True)
        if not args.import_only and not args.publish_saved:
            board = build_board(force=True)
            print(f"Leaderboard through {board['as_of']}: {len(board['rows'])} managers")
        drafts = None
        if args.drafts:
            from services.leaderboard_research import study_index
            studies = study_index()
            drafts = json.loads(args.drafts.read_text())
            for draft in drafts:
                if draft.get('cohort') not in BOARDS or not draft.get('title') or not draft.get('text'):
                    parser.error('Each draft needs a valid cohort, title and text')
                if draft.get('study') not in studies:
                    parser.error('Each draft needs a built research pack')
                draft.setdefault('revision', studies[draft['study']]['revision'])
        if args.publish_saved:
            from services.leaderboard import BOARD_FILE, CACHE_DIR, _read_prices
            from services.leaderboard_research import MANIFEST
            from services.leaderboard_storage import publish
            copied = publish(board_path=CACHE_DIR / BOARD_FILE, studies_path=MANIFEST,
                             prices=_read_prices(), drafts=drafts)
            print(f'Persisted board, price histories and {copied} chart files')
        elif drafts is not None:
            AppCache.__table__.create(db.engine, checkfirst=True)
            db.session.merge(AppCache(key='leaderboard:editorial_backlog', payload=drafts))
            db.session.commit()
            print(f'Imported {len(drafts)} private LinkedIn drafts')


if __name__ == '__main__':
    main()
