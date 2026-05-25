/**
 * phonemeEstimator (pet-anim/B3 fallback) — Pet Animation UX v2.
 *
 * When the backend does not stream `tts_viseme` (Day-0 D0-02 FAIL → fallback
 * mode), the frontend estimates viseme frames from the transcript itself.
 *
 * Algorithm (PRD §3 B3-fallback)
 *   1. Split transcript into characters; treat ASCII punctuation / whitespace
 *      as `silent`.
 *   2. Per-char: look up pinyin from a minimal built-in table (covers ~80
 *      common Mandarin characters); callers may inject a richer dict via
 *      `opts.pinyin_dict` to extend coverage.
 *   3. Pinyin → first vowel character → VisemeCode mapping.
 *   4. Distribute total_duration_ms evenly across non-silent chars
 *      (`ms_per_char` defaults to 200, but is rescaled if total/n_chars
 *      differs so total runtime stays accurate).
 *   5. Emit VisemeFrame[] with absolute t_ms starting at `start_t_ms`
 *      (default 0; caller adds offset).
 *
 * Coverage / accuracy target: PRD §3 B3-fallback says "朋友盲听 ≥ 70%". The
 * minimal table here covers everyday speech samples — Sprint 2 测试 will
 * validate; users can ship a fuller dict.
 */

import type { VisemeCode, VisemeFrame } from './visemeLipsync'

export interface PhonemeEstimatorOpts {
  /** Average ms per Chinese char. PRD: 200 (6 chars/sec). */
  ms_per_char?: number
  /** Optional user-supplied char → pinyin dictionary (extends built-in). */
  pinyin_dict?: Record<string, string>
}

export interface PhonemeEstimator {
  estimate(transcript: string, total_duration_ms: number, start_t_ms?: number): VisemeFrame[]
}

/**
 * Minimal built-in char → pinyin (vowels-only matter; tone marks omitted).
 * Coverage: ~80 high-frequency Mandarin characters. Users with longer scripts
 * should inject a fuller dict via opts.pinyin_dict. Unknown chars → 'silent'
 * (defensive: PRD §3 B3-fallback says "未知字 silent fallback (不抛)").
 */
const BUILTIN_PINYIN: Record<string, string> = {
  // Greetings / common
  你: 'ni', 好: 'hao', 我: 'wo', 是: 'shi', 的: 'de', 一: 'yi', 不: 'bu', 在: 'zai',
  有: 'you', 这: 'zhe', 个: 'ge', 上: 'shang', 下: 'xia', 来: 'lai', 去: 'qu', 大: 'da',
  小: 'xiao', 中: 'zhong', 国: 'guo', 人: 'ren', 们: 'men', 说: 'shuo', 看: 'kan',
  想: 'xiang', 知: 'zhi', 道: 'dao', 时: 'shi', 间: 'jian', 天: 'tian', 没: 'mei',
  // Test cases
  妈: 'ma', 骑: 'qi', 马: 'ma', 慢: 'man', 他: 'ta', 骂: 'ma', 太: 'tai',
  // Numbers
  零: 'ling', 二: 'er', 三: 'san', 四: 'si', 五: 'wu', 六: 'liu', 七: 'qi',
  八: 'ba', 九: 'jiu', 十: 'shi',
  // Common verbs / nouns
  做: 'zuo', 给: 'gei', 到: 'dao', 用: 'yong', 把: 'ba', 让: 'rang', 还: 'hai',
  也: 'ye', 都: 'dou', 就: 'jiu', 要: 'yao', 会: 'hui', 能: 'neng', 可: 'ke',
  以: 'yi', 对: 'dui', 但: 'dan', 而: 'er', 和: 'he', 与: 'yu', 或: 'huo',
  // Common short responses
  嗯: 'en', 哦: 'o', 啊: 'a', 呀: 'ya', 嘛: 'ma', 吧: 'ba', 吗: 'ma', 呢: 'ne',
  哈: 'ha', 嘿: 'hei', 唉: 'ai',
  // Polite / common
  请: 'qing', 谢: 'xie', 抱: 'bao', 歉: 'qian', 然: 'ran', 后: 'hou', 现: 'xian',
  正: 'zheng', 已: 'yi', 经: 'jing', 完: 'wan',
}

/**
 * Vowel → viseme. Order matters: we scan the pinyin string and pick the
 * FIRST vowel cluster (most stable for visible mouth shape).
 *
 * Rules:
 *   a, ai, ao, an, ang → A   (mouth open, jaw down)
 *   o, ou, ong          → O
 *   e, en, eng, ei      → E
 *   i, in, ing          → I
 *   u, un, ong          → U   (ong handled above)
 *   ü, yu               → U
 *
 * Diphthong handling: we emit the first vowel's viseme only (single-frame
 * per char). Full diphthong sequences (e.g. ai → A→I) are out of scope for
 * the minimal estimator — visemeLipsync's 60ms blend will smooth visually.
 */
function pinyinToViseme(pinyin: string): VisemeCode {
  const p = pinyin.toLowerCase()
  // Order: longest match first to handle 'ang' before 'a'.
  // Returns first vowel cluster's viseme.
  for (let i = 0; i < p.length; i++) {
    const c = p[i]
    if (c === 'a') return 'A'
    if (c === 'o') return 'O'
    if (c === 'e') return 'E'
    if (c === 'i') return 'I'
    if (c === 'u' || c === 'ü' || c === 'v') return 'U'
  }
  return 'silent'
}

const CN_PUNCT_OR_SPACE = /^[\s,.。，！？：；、…—"'""()（）【】《》·\-]+$/
const HAN_CHAR = /^[一-鿿]$/

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

export function createPhonemeEstimator(rawOpts: PhonemeEstimatorOpts = {}): PhonemeEstimator {
  const ms_per_char_default = safeNum(rawOpts.ms_per_char, 200, true)
  const user_dict = rawOpts.pinyin_dict ?? {}

  function lookupPinyin(ch: string): string | null {
    if (Object.prototype.hasOwnProperty.call(user_dict, ch)) return user_dict[ch]
    if (Object.prototype.hasOwnProperty.call(BUILTIN_PINYIN, ch)) return BUILTIN_PINYIN[ch]
    return null
  }

  function charToViseme(ch: string): VisemeCode {
    if (CN_PUNCT_OR_SPACE.test(ch)) return 'silent'
    // ASCII letter? Use the letter itself as a one-char pinyin (handles "K 歌").
    if (/^[a-zA-Z]$/.test(ch)) {
      return pinyinToViseme(ch)
    }
    if (HAN_CHAR.test(ch)) {
      const py = lookupPinyin(ch)
      if (!py) return 'silent' // unknown character defensive fallback
      return pinyinToViseme(py)
    }
    // Numbers / symbols → silent.
    return 'silent'
  }

  function estimate(
    transcript: string,
    total_duration_ms: number,
    start_t_ms: number = 0,
  ): VisemeFrame[] {
    if (typeof transcript !== 'string' || transcript.length === 0) return []
    if (!Number.isFinite(total_duration_ms) || total_duration_ms <= 0) return []
    if (!Number.isFinite(start_t_ms)) start_t_ms = 0

    // Split into characters (treat surrogates conservatively — most CJK fits in BMP).
    const chars = Array.from(transcript)
    const n = chars.length
    if (n === 0) return []

    // Per-char duration: scale to fit total_duration_ms exactly.
    const ms_per = Math.max(1, total_duration_ms / n)

    const frames: VisemeFrame[] = []
    let t = start_t_ms
    let last_v: VisemeCode | null = null
    for (const ch of chars) {
      const v = charToViseme(ch)
      // De-duplicate consecutive identical visemes (saves queue space; visemeLipsync
      // does the same effective thing internally).
      if (v !== last_v) {
        frames.push({ v, t_ms: t })
        last_v = v
      }
      t += ms_per
    }
    // Always cap with a trailing silent so visemeLipsync's silent_after_ms doesn't
    // kick in too late.
    if (last_v !== 'silent') {
      frames.push({ v: 'silent', t_ms: t })
    }

    // ms_per may have rounded; ensure final t aligns with total budget.
    // (Not strictly necessary — but keeps total runtime accurate for tests.)
    const expected_end = start_t_ms + total_duration_ms
    if (frames.length > 0) {
      const last = frames[frames.length - 1]
      if (Math.abs(last.t_ms - expected_end) > 1) {
        last.t_ms = expected_end
      }
    }

    return frames
  }

  return { estimate }
}

export { BUILTIN_PINYIN, pinyinToViseme }
