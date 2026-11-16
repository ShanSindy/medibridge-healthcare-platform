import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_API_BASE_URL, resolveApiBaseUrl, resolveDataSource } from './api'

describe('resolveDataSource', () => {
  it('selects API mode only for "api"', () => {
    expect(resolveDataSource('api')).toBe('api')
    expect(resolveDataSource('  API ')).toBe('api')
  })

  it('defaults to demo mode when unset, empty or unrecognised', () => {
    expect(resolveDataSource(undefined)).toBe('demo')
    expect(resolveDataSource('')).toBe('demo')
    expect(resolveDataSource('demo')).toBe('demo')
    expect(resolveDataSource('live')).toBe('demo')
  })
})

describe('resolveApiBaseUrl', () => {
  it('defaults to http://localhost:8000', () => {
    expect(DEFAULT_API_BASE_URL).toBe('http://localhost:8000')
    expect(resolveApiBaseUrl(undefined)).toBe(DEFAULT_API_BASE_URL)
    expect(resolveApiBaseUrl('   ')).toBe(DEFAULT_API_BASE_URL)
  })

  it('trims whitespace and trailing slashes', () => {
    expect(resolveApiBaseUrl(' https://api.example.org/ ')).toBe('https://api.example.org')
    expect(resolveApiBaseUrl('/api/')).toBe('/api')
  })

  it.each([
    ['a Windows path from shell path conversion', 'C:/Program Files/Git/api'],
    ['a bare host without a scheme', 'localhost:8000'],
    ['an unsupported scheme', 'ftp://api.example.org'],
    ['a protocol-relative URL', '//api.example.org'],
    ['a relative path', 'api'],
  ])('falls back to the default for %s, with a warning', (_label, value) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(resolveApiBaseUrl(value)).toBe(DEFAULT_API_BASE_URL)
    expect(warn).toHaveBeenCalledOnce()
  })
})
