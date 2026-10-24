"""Send the approval note to accounts activated from the waiting list.

Dry-run by default. Pass --send to actually email. The recipient list is
the snapshot stored in app_cache when the accounts were activated, so the
script never mails the whole user table by accident.
"""

import argparse
import json
import sys

from app import app
from models.database import db, AppCache
from models.auth import User
from services.email_service import email_service

KEY = "approval_notice_pending"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="actually deliver the emails")
    args = parser.parse_args()

    with app.app_context():
        row = db.session.get(AppCache, KEY)
        emails = list(row.payload or []) if row else []
        if not emails:
            print("No pending approval notices.")
            return 0

        recipients = User.query.filter(User.email.in_(emails)).all()
        print(f"{len(recipients)} recipient(s):")
        for user in recipients:
            print(f"  {user.email}  {user.first_name or ''}".rstrip())

        if not args.send:
            print("\nDry run. Re-run with --send to deliver.")
            return 0

        sent = []
        for user in recipients:
            ok = email_service.send_account_approved(user.email, user.first_name)
            print(f"  {'sent' if ok else 'FAILED'}  {user.email}")
            if ok:
                sent.append(user.email)

        remaining = [email for email in emails if email not in sent]
        if remaining:
            row.payload = remaining
            db.session.commit()
            print(f"\n{len(sent)} sent, {len(remaining)} remaining.")
        else:
            db.session.delete(row)
            db.session.commit()
            print(f"\n{len(sent)} sent. Queue cleared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
