"""P4 state.db 启动守门：备份 → 迁移 → 失败回滚。

职责（和 migrator.py 的分工）：
  * ``migrator.py`` 只关心"把 SQL 文件按序跑起来"，不做备份恢复
  * ``schema.py``（本文件）负责"在 DB 被动过之前把 .bak 留下，失败时
    从 .bak 还原"——这是 R12 风险兜底，独立出来以便主流程（未来
    MemoryManager.initialize）走简单路径 `initialize_state_db(p)`。

Degrade contract：
  * 成功 → return（DB 就位，v9+）
  * 失败 → 从 .bak 恢复 + raise ``InitializeError``
  * 上层（MemoryManager / main.py）收到 InitializeError MUST 把 L2/L3
    关掉只跑 L1 文件记忆。这一步在 P4-S4 的 manager.py 里落实。

Ref:
  * design.md §D-MIGRATE-1 + R12
  * spec "Schema Migration v8 → v9" Scenario "Migration failure rollback"
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from deskpet.memory.migrator import (
    MigrationError,
    backup_db,
    ensure_v9,
)

log = logging.getLogger(__name__)


class InitializeError(RuntimeError):
    """L2 初始化失败——调用方需降级启动（无 L2/L3）。"""


async def initialize_state_db(db_path: str | Path) -> None:
    """确保 ``db_path`` 指向一个 v9+ 的 state.db。

    行为顺序（故意冗长，便于 bug 定位）：
      1. 父目录 mkdir（tmp_path 场景）
      2. 如果 db 文件已存在 → ``backup_db`` 拷一份 ``.bak.<ts>``
         * 新库（文件不存在）跳过，这种情况一定是 fresh install，
           迁移失败也没什么能"丢"，只需要上层降级。
      3. 调 ``ensure_v9(db)``
      4. 成功 → return
      5. 失败：
         * 如果之前有 .bak，把原 db 文件删掉，.bak 复制回 db_path
           * 此举确保 state.db 回到迁移前状态——即便 ``ensure_v9``
             只跑了一半导致 schema 半破也没关系
         * 无论 .bak 是否存在，最后都 raise ``InitializeError``
           * 上层据此决定进入降级模式

    Notes：
      * 本函数 **不** 关心 sqlite-vec / messages_vec —— 那些由
        SessionDB.initialize() 负责，且失败只 warn 不 raise。
      * 单独承担"备份 + 回滚"职责，不做任何 schema 读写校验。校验放在
        SessionDB 的启动自检（tables_exist 等）。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    bak_path: Path | None = None
    if db_path.exists():
        try:
            bak_path = await backup_db(db_path)
            log.info("state.db backed up before migration: %s", bak_path)
        except OSError as exc:
            # 备份失败通常是磁盘满 / 权限问题。不强行迁移（否则迁失败就
            # 真的丢数据）——直接 raise 让上层降级。
            log.error("state.db backup failed: %s", exc)
            raise InitializeError(f"cannot backup state.db: {exc}") from exc

    try:
        applied = await ensure_v9(db_path)
        if applied:
            log.info("state.db migrations applied: %s", applied)
        else:
            log.debug("state.db migrations already up-to-date")
    except (MigrationError, Exception) as exc:  # noqa: BLE001  # 故意 broad
        # 任何迁移异常都走回滚。包括 sqlite3.Error / OSError / 自定义。
        log.error("state.db migration failed: %s; attempting rollback", exc)
        if bak_path is not None and bak_path.exists():
            try:
                # 直接覆盖；WAL/SHM 副本已经 checkpoint 回主文件
                # （aiosqlite close 保证），不必单独处理。
                if db_path.exists():
                    db_path.unlink()
                shutil.copy2(bak_path, db_path)
                log.info("state.db restored from %s", bak_path)
            except OSError as restore_exc:
                # 回滚再失败就真的只能降级了——原 .bak 还在盘上，
                # 支持 bundle 可让用户手动 rename。
                log.error(
                    "state.db rollback failed: %s (manual restore may be needed from %s)",
                    restore_exc,
                    bak_path,
                )
        else:
            log.warning("state.db had no backup to restore (fresh install)")
        raise InitializeError(f"state.db initialization failed: {exc}") from exc
