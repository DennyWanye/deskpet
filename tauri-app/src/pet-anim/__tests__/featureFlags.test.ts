/**
 * featureFlags.test.ts — TDD §4.9 (TC-F-01..05)
 */
import { describe, expect, it } from 'vitest'
import { isEnabled, FLAG_KEYS } from '../featureFlags'

function makeStorage(initial: Record<string, string> = {}): Storage {
  const m = new Map<string, string>(Object.entries(initial))
  return {
    get length() {
      return m.size
    },
    clear() {
      m.clear()
    },
    getItem(k: string) {
      return m.has(k) ? (m.get(k) as string) : null
    },
    key(i: number) {
      return Array.from(m.keys())[i] ?? null
    },
    removeItem(k: string) {
      m.delete(k)
    },
    setItem(k: string, v: string) {
      m.set(k, v)
    },
  } as Storage
}

describe('featureFlags', () => {
  it('TC-F-01 (P0) localStorage 空 → 所有 isEnabled true', () => {
    const s = makeStorage()
    for (const flag of Object.keys(FLAG_KEYS) as Array<keyof typeof FLAG_KEYS>) {
      expect(isEnabled(flag, s)).toBe(true)
    }
  })

  it('TC-F-02 (P0) all=off → 所有 false (hard kill)', () => {
    const s = makeStorage({ [FLAG_KEYS.all]: 'off' })
    for (const flag of Object.keys(FLAG_KEYS) as Array<keyof typeof FLAG_KEYS>) {
      expect(isEnabled(flag, s)).toBe(false)
    }
  })

  it('TC-F-03 (P0) all=off + individual=on → 全 false (hard kill 不可覆盖)', () => {
    const s = makeStorage({
      [FLAG_KEYS.all]: 'off',
      [FLAG_KEYS.perlin]: 'on',
      [FLAG_KEYS.gaze]: 'on',
    })
    expect(isEnabled('perlin', s)).toBe(false)
    expect(isEnabled('gaze', s)).toBe(false)
  })

  it('TC-F-04 (P0) perlin=off 单独 → 只 perlin false', () => {
    const s = makeStorage({ [FLAG_KEYS.perlin]: 'off' })
    expect(isEnabled('perlin', s)).toBe(false)
    expect(isEnabled('blink', s)).toBe(true)
    expect(isEnabled('gaze', s)).toBe(true)
    expect(isEnabled('all', s)).toBe(true)
  })

  it('TC-F-05 (P1) storage throw → 默认 on', () => {
    const throwing: Storage = {
      get length() {
        return 0
      },
      clear() {},
      getItem() {
        throw new Error('quota')
      },
      key() {
        return null
      },
      removeItem() {},
      setItem() {},
    } as Storage
    expect(isEnabled('perlin', throwing)).toBe(true)
    expect(isEnabled('all', throwing)).toBe(true)
  })
})
