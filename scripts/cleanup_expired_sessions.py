from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.models import RefreshSession


def cleanup(db: Session, *, grace_days: int = 7) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    deleted = (
        db.query(RefreshSession)
        .filter(RefreshSession.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.flush()
    return deleted


def main() -> None:
    from app.core.db import get_db
    with get_db() as session:
        n = cleanup(session, grace_days=7)
        session.commit()
        print(f"deleted {n} expired refresh_sessions rows")


if __name__ == "__main__":
    main()
