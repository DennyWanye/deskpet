"""P4-S3 L3 混合召回 + RRF 融合。

职责
----
把 ``SessionDB`` 的四个召回信号（向量相似度 / FTS5 全文 / recency /
salience）并行跑一次 fan-out，再用 Reciprocal Rank Fusion 合并成一份
按融合分降序的 ``Hit`` 列表。

四路来源、两个外部依赖、一次融合：

* ``Retriever(session_db, embedder, policy)`` 构造 —— 依赖 P4-S1 ``SessionDB``
  和 P4-S2 ``Embedder``，两者都是只读依赖（本模块不改它们的状态，除了
  通过公开 API 更新 salience）。
* ``recall(query, top_k)`` 对外入口：fan-out 四个信号，RRF 融合返回 top_k。
* ``_boost_salience`` 在 recall 命中条目上打 +0.05 → 下次该条更容易
  浮到顶部（见 spec "Memory Decay and Salience"）。
* ``daily_decay`` 在启动时跑一次：``salience *= exp(-lambda * days_since_touch)``。

Fusion 算法（RRF，classic k=60）::

    rrf_score(item) = Σ  weight[source] / (k + rank[source])
                     source ∈ item.sources

源列表里有命中就计入该源贡献，没命中则跳过。weight 来自 RetrievalPolicy
（vec 0.5 / fts 0.3 / recency 0.15 / salience 0.05 —— 见 spec "Hybrid
Retrieval with RRF"）。k=60 是 Cormack 原始论文的默认值，对几十到几万
结果大小都工作得很好；用常量 ``_RRF_K`` 便于单测调低验证融合分数可预测。

降级路径（spec "L3 failure degrades gracefully"）
------------------------------------------------
向量层任何异常（embedder 未 ready、sqlite-vec 没 load、connection error）
都被捕获并只 log warning，recall() 继续用剩下三个信号返回结果，**不抛**。
FTS / recency / salience 三条路彼此也是同样的失败隔离策略 —— 一条路挂
不拖垮其它。

Ref
---
* spec ``memory-system`` Requirement "Hybrid Retrieval with RRF"
  + Scenarios "Semantic query matches non-literal match" / "Recent items boosted"
* spec Requirement "Memory Decay and Salience"
* design.md §D-ARCH-1 三层记忆、§D-IMPL-2 sqlite-vec 选择
* tasks.md §5 (P4-S3) 所有子项
"""
from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Iterable

import aiosqlite
import numpy as np

from deskpet.memory.embedder import EMBEDDING_DIM, Embedder
from deskpet.memory.session_db import SessionDB

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# RRF 常量
# ----------------------------------------------------------------------
# Cormack 等人提出的 Reciprocal Rank Fusion 经典 k 参数。k=60 在大多数
# IR 场景（几百到几万条候选）都表现良好；改小会放大"靠前条目优势"，
# 改大会拉平各 rank 间差距。单测里可以通过函数参数覆盖，便于验证融合
# 分数的可预测性。
_RRF_K = 60

# ----------------------------------------------------------------------
# Salience boost / decay 常量
# ----------------------------------------------------------------------
# recall 命中时给 salience 的默认增量（spec "Recall boosts salience" 要求 0.05）
_DEFAULT_SALIENCE_BOOST = 0.05
# salience 上限：不要让热门条目无上限膨胀，超过则 clamp 到 1.0
_SALIENCE_MAX = 1.0
# salience 下限：decay 衰减极限，保留一点点存在感（避免彻底归零后 rank 退出）
_SALIENCE_MIN = 0.0
# 一天的秒数，daily_decay 公式里 days_since_touch 的分母
_SECONDS_PER_DAY = 86400.0


# ======================================================================
# Public dataclasses
# ======================================================================


@dataclass(frozen=True)
class Hit:
    """一条召回结果。

    * ``score`` 是 RRF 融合后分数（越大越相关），仅用于排序与 UI 展示，
      不要当绝对相似度解读。
    * ``source`` 标明这条 Hit **最主要** 来自哪个信号；多源命中时取
      贡献 RRF 分最大的那个源（便于 debug / Context Trace UI）。
      取值范围：``"vec" | "fts" | "recency" | "salience"``。
    """

    message_id: int
    score: float
    text: str
    ts: float
    source: str


@dataclass
class RetrievalPolicy:
    """召回权重配置。

    默认值来自 spec "Hybrid Retrieval with RRF" / config.toml ``[memory.rrf]``。
    调用方可以传完全自定义的实例（例如某个 task_type 下偏重 FTS），但
    权重**不**要求归一化——RRF 本质是 rank 加权，权重更像"重要性旋钮"
    而非概率分布。

    Attributes
    ----------
    top_k:
        fan-out 每个信号拉回的候选条数（单路）。融合后返回的最终条数由
        ``recall(..., top_k=...)`` 参数控制，通常也用这个值。
    vec_weight / fts_weight / recency_weight / salience_weight:
        对应信号在 RRF 融合时的乘法权重；总和不要求等于 1.0。
    """

    top_k: int = 20
    vec_weight: float = 0.5
    fts_weight: float = 0.3
    recency_weight: float = 0.15
    salience_weight: float = 0.05


# ======================================================================
# Retriever
# ======================================================================


class Retriever:
    """Hybrid recall: vec + fts + recency + salience → RRF fusion。

    Usage::

        retriever = Retriever(session_db, embedder, RetrievalPolicy())
        hits = await retriever.recall("我上次说的那个袜子", top_k=10)
        for h in hits:
            print(h.message_id, h.score, h.source, h.text[:40])
    """

    def __init__(
        self,
        session_db: SessionDB,
        embedder: Embedder,
        policy: RetrievalPolicy | None = None,
    ) -> None:
        self._db = session_db
        self._embedder = embedder
        self._policy = policy or RetrievalPolicy()

    @property
    def policy(self) -> RetrievalPolicy:
        return self._policy

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def recall(self, query: str, top_k: int | None = None) -> list[Hit]:
        """四路 fan-out → RRF 融合 → salience boost → 返回 top_k。

        Parameters
        ----------
        query:
            用户自然语言 query。空串或只含空白时 **不** 抛，而是直接
            返回空 list（agent 有时会用空 query 探测 embedder 状态）。
        top_k:
            最终返回条数上限；None 时取 ``policy.top_k``。每路 fan-out
            内部都拉 ``policy.top_k`` 条候选（给 RRF 足够合并空间），
            最后裁到 top_k。

        Returns
        -------
        list[Hit]
            按 RRF score 降序。允许返回少于 top_k（候选不足时）。

        Failure isolation
        -----------------
        * 任一信号抛异常 → 只 log warning，剩下的信号继续融合。
        * embedder 未 ready / sqlite-vec 不可用 → 自动跳过 vec 路。
        * 所有信号都挂 → 返回空 list（调用方据此降级）。
        """
        if not query or not query.strip():
            return []

        effective_top_k = top_k if top_k is not None else self._policy.top_k
        fanout_k = max(effective_top_k, self._policy.top_k)

        # 向量路需要先把 query 转成 vector；失败就跳过这路而不是炸整个 recall。
        query_vec = await self._safe_embed_query(query)

        # fan-out：四路并行。return_exceptions=True 让单路挂不影响其它。
        raw_results: list[list[tuple[int, float]] | BaseException] = (
            await asyncio.gather(
                self._safe_call(
                    "vec",
                    self._vec_recall(query_vec, fanout_k) if query_vec is not None else _empty(),
                ),
                self._safe_call("fts", self._fts_recall(query, fanout_k)),
                self._safe_call("recency", self._recency_recall(fanout_k)),
                self._safe_call("salience", self._salience_recall(fanout_k)),
                return_exceptions=False,  # _safe_call 已内化异常
            )
        )

        vec_list, fts_list, recency_list, salience_list = (
            _coerce_list(raw_results[0]),
            _coerce_list(raw_results[1]),
            _coerce_list(raw_results[2]),
            _coerce_list(raw_results[3]),
        )

        fused = _rrf_fuse(
            [
                ("vec", vec_list, self._policy.vec_weight),
                ("fts", fts_list, self._policy.fts_weight),
                ("recency", recency_list, self._policy.recency_weight),
                ("salience", salience_list, self._policy.salience_weight),
            ],
            k=_RRF_K,
        )
        # fused 里只有 message_id + score + dominant source，需要补 text/ts
        # 才能构造 Hit。一次 SQL 批量拉。
        if not fused:
            return []

        top_items = fused[:effective_top_k]
        ids = [mid for mid, _, _ in top_items]
        metadata = await self._fetch_message_meta(ids)

        hits: list[Hit] = []
        for mid, score, source in top_items:
            meta = metadata.get(mid)
            if meta is None:
                # message 在 fan-out 和 fetch 之间被删了 —— 罕见，跳过。
                continue
            hits.append(
                Hit(
                    message_id=mid,
                    score=score,
                    text=meta["content"],
                    ts=meta["created_at"],
                    source=source,
                )
            )

        # 命中 boost：spec 明确要求 salience += 0.05 + decay_last_touch=now()。
        # 放在构造 hits 之后、返回之前；boost 失败只 log，不影响返回。
        try:
            await self._boost_salience(hits, boost=_DEFAULT_SALIENCE_BOOST)
        except Exception as exc:  # noqa: BLE001
            log.warning("salience boost failed: %s (hits=%d)", exc, len(hits))

        return hits

    # ------------------------------------------------------------------
    # Fan-out primitives
    # ------------------------------------------------------------------

    async def _vec_recall(
        self, query_vector: list[float] | np.ndarray, top_k: int
    ) -> list[tuple[int, float]]:
        """sqlite-vec MATCH on ``messages_vec``. Returns [(message_id, distance), ...]。

        失败场景（全部会 raise 给 ``_safe_call`` 吃掉）：
          * sqlite_vec 未安装 / load_extension 失败
          * messages_vec 虚拟表不存在（DB 建表失败 / 未初始化）
          * query_vector 维度不符
        """
        if query_vector is None:
            return []

        vec = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if vec.shape[0] != EMBEDDING_DIM:
            raise ValueError(
                f"query_vector dim {vec.shape[0]} != expected {EMBEDDING_DIM}"
            )
        payload = vec.tobytes()
        db_path = str(self._db._db_path)

        def _sync_query() -> list[tuple[int, float]]:
            # sqlite-vec 需要原生 sqlite3 connection（enable_load_extension），
            # aiosqlite 要用 executor 跑一遍。单次查询短连接即可。
            import sqlite_vec  # type: ignore

            conn = sqlite3.connect(db_path)
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                # vec0 虚表 KNN 语法：WHERE embedding MATCH ? AND k = ?
                # ORDER BY distance 是必须的 hint，否则返回顺序未定义。
                rows = conn.execute(
                    "SELECT message_id, distance FROM messages_vec "
                    "WHERE embedding MATCH ? AND k = ? "
                    "ORDER BY distance",
                    (payload, top_k),
                ).fetchall()
                return [(int(r[0]), float(r[1])) for r in rows]
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_query)

    async def _fts_recall(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """FTS5 ``MATCH ... ORDER BY rank``. Returns [(message_id, rank), ...]。

        用已有的 ``SessionDB.search_fts``；返回字段里 ``rank`` 是 FTS5
        内建分（越小越相关）。我们只需要"按 rank 升序的 message_id 列表"，
        RRF 融合看的是顺序而非绝对值。

        tokenize 是 trigram —— ≤2 字符 query 不会命中，这是 tokenizer
        固有限制而非 bug。FTS5 对非法 query（比如只有保留字符）会抛
        ``OperationalError``，被 ``_safe_call`` 捕获 → 当作 0 条结果处理。
        """
        if not query or not query.strip():
            return []
        # FTS5 对某些字符敏感（引号、冒号、括号等保留给 query syntax），
        # 把 query 塞进短语引号，让整串被当字面量匹配，避免语法报错。
        # trigram tokenizer 下短语匹配依然是子串命中（tokenizer 拆 3-gram
        # 时不看引号），对普通 chat query 最稳。
        quoted = _quote_fts_phrase(query)
        try:
            rows = await self._db.search_fts(quoted, limit=top_k)
        except sqlite3.OperationalError as exc:
            # 极端情况：query 经 quote 后仍不合法。降级为空结果，让
            # 其它信号继续融合。
            log.debug("fts search failed for %r: %s", query, exc)
            return []
        # rank 是 float（SQLite 返回 double），越小越相关。
        return [(int(r["id"]), float(r.get("rank") or 0.0)) for r in rows]

    async def _recency_recall(self, top_k: int) -> list[tuple[int, float]]:
        """最近 top_k 条消息。Returns [(message_id, created_at_desc_rank_key), ...]。

        返回的 float 就是 created_at 本身（越大越新）——RRF 只看顺序，
        不看绝对值，但保留时间戳让 debug 更直观。
        """
        db_path = str(self._db._db_path)
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT id, created_at FROM messages "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (top_k,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [(int(r[0]), float(r[1])) for r in rows]

    async def _salience_recall(self, top_k: int) -> list[tuple[int, float]]:
        """salience 最高的 top_k 条。Returns [(message_id, salience), ...]。"""
        db_path = str(self._db._db_path)
        async with aiosqlite.connect(db_path) as db:
            # salience IS NULL 应该不会出现（schema DEFAULT 0.5），但保险
            # 起见用 COALESCE；NULL 视作 0.5 保持和 schema 一致。
            cursor = await db.execute(
                "SELECT id, COALESCE(salience, 0.5) FROM messages "
                "ORDER BY COALESCE(salience, 0.5) DESC, id DESC LIMIT ?",
                (top_k,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [(int(r[0]), float(r[1])) for r in rows]

    # ------------------------------------------------------------------
    # Salience feedback
    # ------------------------------------------------------------------

    async def _boost_salience(
        self, hits: Iterable[Hit], boost: float = _DEFAULT_SALIENCE_BOOST
    ) -> None:
        """对 hits 里的每条 message 做 ``salience += boost`` + ``decay_last_touch=now()``.

        批量执行（单个 UPDATE ... WHERE id IN (...)），比循环快且避免多个
        小写事务。salience 上限 clamp 到 ``_SALIENCE_MAX``（默认 1.0）。
        """
        ids = [h.message_id for h in hits]
        if not ids:
            return

        now = time.time()
        placeholders = ",".join("?" for _ in ids)
        db_path = str(self._db._db_path)

        # NB: 这里没走 SessionDB.update_salience 逐条更新 —— recall 每次可能
        # 命中 20 条，每条一次事务开销太大。直接一条 UPDATE 批量做。
        # MIN(..., _SALIENCE_MAX) 防 salience 无上限膨胀。
        sql = (
            f"UPDATE messages SET "
            f"salience = MIN(COALESCE(salience, 0.5) + ?, ?), "
            f"decay_last_touch = ? "
            f"WHERE id IN ({placeholders})"
        )
        params: tuple = (boost, _SALIENCE_MAX, now, *ids)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute(sql, params)
            await db.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _safe_embed_query(self, query: str) -> np.ndarray | None:
        """把 query 编成向量；embedder 未 ready / 抛错时返回 None。"""
        if not self._embedder.is_ready():
            # Non-ready 不是 bug（首装还在下 BGE-M3）；日志降到 debug 别刷屏。
            log.debug("embedder not ready, skipping vec recall")
            return None
        try:
            vecs = await self._embedder.encode([query])
        except Exception as exc:  # noqa: BLE001
            log.warning("embedder.encode failed for query (%s); skipping vec recall", exc)
            return None
        if vecs.shape != (1, EMBEDDING_DIM):
            log.warning(
                "embedder returned unexpected shape %s; skipping vec recall",
                vecs.shape,
            )
            return None
        return vecs[0]

    async def _safe_call(
        self,
        name: str,
        coro,
    ) -> list[tuple[int, float]]:
        """跑一个 fan-out 协程，任何异常吞掉返回空 list + log warning。

        把每路的"失败隔离"集中到一个位置，recall() 主体就只需要看结果列表。
        """
        try:
            result = await coro
            return list(result) if result else []
        except Exception as exc:  # noqa: BLE001
            log.warning("recall source=%s failed: %s", name, exc)
            return []

    async def _fetch_message_meta(
        self, message_ids: list[int]
    ) -> dict[int, dict]:
        """批量拿 {id: {content, created_at}}. 空列表返回 {}。"""
        if not message_ids:
            return {}
        placeholders = ",".join("?" for _ in message_ids)
        db_path = str(self._db._db_path)
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                f"SELECT id, content, created_at FROM messages "
                f"WHERE id IN ({placeholders})",
                tuple(message_ids),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return {
            int(r[0]): {"content": r[1], "created_at": float(r[2])}
            for r in rows
        }


# ======================================================================
# RRF fusion helper (pure function, no IO)
# ======================================================================


def _rrf_fuse(
    sources: list[tuple[str, list[tuple[int, float]], float]],
    *,
    k: int = _RRF_K,
) -> list[tuple[int, float, str]]:
    """Reciprocal Rank Fusion。

    Parameters
    ----------
    sources:
        list of ``(source_name, ranked_items, weight)``。``ranked_items``
        是 ``[(message_id, raw_score), ...]`` 并已按该源的"越前越好"顺序
        排好（vec 按 distance 升序、fts 按 rank 升序、recency 按 ts 降序、
        salience 按 salience 降序）。**重要：本函数不重排 ranked_items**，
        直接用其位置作 rank 来源。
    k:
        RRF k 常量；越小前排贡献越大。默认 _RRF_K=60。

    Returns
    -------
    list[(message_id, fused_score, dominant_source)]
        按 fused_score 降序。``dominant_source`` 是贡献分最大的那一源
        （用于 UI 显示 / debug），非唯一来源。
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k}")

    # 按 id 累计融合分 & 记录每源贡献，便于挑 dominant_source。
    scores: dict[int, float] = {}
    # contributions[mid] = { source_name: contribution_score }
    contributions: dict[int, dict[str, float]] = {}

    for source_name, items, weight in sources:
        if weight <= 0 or not items:
            continue
        for rank_idx, (mid, _raw) in enumerate(items):
            # RRF 经典公式：1 / (k + rank)。rank 从 1 起算（原论文）；
            # 这里 rank_idx 从 0 起，所以 k + rank_idx + 1。
            contribution = weight / (k + rank_idx + 1)
            scores[mid] = scores.get(mid, 0.0) + contribution
            contributions.setdefault(mid, {})[source_name] = contribution

    if not scores:
        return []

    # 对每个 mid 挑 dominant source（贡献最大的源）。同分取固定优先级
    # vec > fts > recency > salience，让结果 deterministic。
    priority = {"vec": 0, "fts": 1, "recency": 2, "salience": 3}

    def _dominant(mid: int) -> str:
        c = contributions.get(mid, {})
        if not c:
            return "vec"  # 理论不可达（mid 一定有至少一个贡献源）
        # max by (value, -priority) — 值大者优先；同值按 priority 升序挑。
        return max(
            c.items(),
            key=lambda kv: (kv[1], -priority.get(kv[0], 99)),
        )[0]

    fused = [
        (mid, score, _dominant(mid))
        for mid, score in scores.items()
    ]
    # 最终按 fused_score 降序；同分按 message_id 升序作稳定 tie-break。
    fused.sort(key=lambda t: (-t[1], t[0]))
    return fused


# ======================================================================
# Daily decay task
# ======================================================================


async def daily_decay(
    session_db: SessionDB,
    decay_lambda: float = 0.02,
    *,
    now: float | None = None,
) -> int:
    """按 ``salience *= exp(-lambda * days_since_touch)`` 衰减所有消息的 salience。

    启动时跑一次（spec "Daily decay on idle memory"）。

    SQLite 本身没有 ``exp()``（不加数学扩展时），所以在 Python 侧拉所有
    ``(id, salience, decay_last_touch)`` 然后算好 new_salience 一次性 UPDATE
    回去。10 万条量级下单次 SELECT + executemany UPDATE 在秒级以内。

    Parameters
    ----------
    session_db:
        初始化过的 SessionDB 实例（读 ``_db_path``）。
    decay_lambda:
        衰减系数，单位 1/天。spec 例子是 0.02：30 天后 salience 衰减到
        原值的 ``exp(-0.02 * 30) ≈ 0.549`` 倍。
    now:
        覆盖"当前时间"（单测用），默认 ``time.time()``。

    Returns
    -------
    int
        实际被更新的行数（salience 发生变化的条目数）。

    Notes
    -----
    * ``decay_last_touch IS NULL`` 视作 "从未被 recall 过"，衰减时用
      创建时间 ``created_at`` 当触摸时间，避免冷消息永久保留在 0.5 分。
    * salience 最终 clamp 到 ``[_SALIENCE_MIN, _SALIENCE_MAX]``，避免浮点
      误差把极小值推到负数。
    * 本函数对 L3 降级场景安全：即使 sqlite-vec 不可用也能跑（只动 messages
      表，和 messages_vec 无关）。
    """
    now_ts = now if now is not None else time.time()
    db_path = str(session_db._db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT id, COALESCE(salience, 0.5), "
            "COALESCE(decay_last_touch, created_at) "
            "FROM messages"
        )
        rows = await cursor.fetchall()
        await cursor.close()

        updates: list[tuple[float, int]] = []
        for mid, salience, last_touch in rows:
            if last_touch is None:
                continue
            days_since = max(0.0, (now_ts - float(last_touch)) / _SECONDS_PER_DAY)
            # exp(-lambda * days) ∈ (0, 1] —— 衰减因子
            factor = math.exp(-decay_lambda * days_since)
            new_salience = float(salience) * factor
            # clamp 到 [MIN, MAX]，避免浮点噪声导致 < 0
            new_salience = max(_SALIENCE_MIN, min(_SALIENCE_MAX, new_salience))
            # 只更新真的有变化的行（差 > 1e-6 才算"变了"），减少写放大
            if abs(new_salience - float(salience)) > 1e-6:
                updates.append((new_salience, int(mid)))

        if not updates:
            return 0

        await db.execute("PRAGMA busy_timeout=5000")
        await db.executemany(
            "UPDATE messages SET salience = ? WHERE id = ?",
            updates,
        )
        await db.commit()
        return len(updates)


# ======================================================================
# Small helpers
# ======================================================================


def _coerce_list(x) -> list[tuple[int, float]]:
    """gather 结果可能是 list 或 exception；归一化成 list。

    ``_safe_call`` 已经把 exception 吃了，理论上只会拿到 list；此处兜底
    是为了防范 return_exceptions 未来如果改 True 时的行为变化。
    """
    if isinstance(x, BaseException):
        return []
    if x is None:
        return []
    return list(x)


async def _empty() -> list[tuple[int, float]]:
    """vec 路被跳过时提供一个合法的 awaitable，保持 gather 形状稳定。"""
    return []


def _quote_fts_phrase(query: str) -> str:
    """把 query 包成 FTS5 字面短语。

    FTS5 里双引号 `"..."` 表示 phrase match；内部双引号需要 double-escape。
    trigram tokenizer 拆 3-gram 时忽略分隔符，所以 phrase 模式下依然是子串
    匹配（不需要字面连续）。这样 agent 层传 "python 错误" 这种带空格的
    自然语言 query 也能稳跑。
    """
    escaped = query.replace('"', '""')
    return f'"{escaped}"'
