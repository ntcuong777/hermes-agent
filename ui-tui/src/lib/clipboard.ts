import { execFile, spawn } from 'node:child_process'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const CLIPBOARD_MAX_BUFFER = 4 * 1024 * 1024

// ── Clipboard image read (macOS + Linux; parity with hermes_cli/clipboard.py) ────────────────────────

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

const isPng = (data: Buffer): boolean => data.subarray(0, 8).equals(PNG_SIGNATURE)

export type ClipboardImageRunner = (
  cmd: string,
  args: string[],
  opts: { maxBuffer: number; timeout: number }
) => Promise<Buffer>

const defaultImageRunner: ClipboardImageRunner = (cmd, args, opts) =>
  new Promise((resolve, reject) => {
    execFile(cmd, args, { ...opts, encoding: 'buffer' }, (err, stdout) => {
      if (err) {
        reject(err)
      } else {
        resolve(stdout as Buffer)
      }
    })
  })

/** The per-platform commands that may hold a clipboard PNG, first-wins order. */
export function clipboardImageCommands(
  platform: NodeJS.Platform,
  env: NodeJS.ProcessEnv
): Array<[string, string[]]> {
  if (platform === 'darwin') {
    return [['osascript', ['-e', 'the clipboard as «class PNGf»']]]
  }

  const wayland = env.XDG_SESSION_TYPE === 'wayland' || Boolean(env.WAYLAND_DISPLAY)
  const wl: [string, string[]] = ['wl-paste', ['--type', 'image/png']]
  const xclip: [string, string[]] = ['xclip', ['-selection', 'clipboard', '-t', 'image/png', '-o']]

  return wayland ? [wl, xclip] : [xclip, wl]
}

/**
 * The clipboard image as PNG bytes, or null when no command produced one (no tool, no image,
 * or not a PNG). Runs in the TUI's own process — a GUI-session terminal has pasteboard access
 * a launchd-managed gateway may lack, which is the fallback this exists for.
 */
export async function readClipboardImagePng(
  platform: NodeJS.Platform = process.platform,
  env: NodeJS.ProcessEnv = process.env,
  run: ClipboardImageRunner = defaultImageRunner
): Promise<Buffer | null> {
  for (const [cmd, args] of clipboardImageCommands(platform, env)) {
    try {
      const raw = await run(cmd, args, { maxBuffer: CLIPBOARD_MAX_BUFFER * 2, timeout: 4000 })

      // osascript appends a newline to the PNGf dump: strip one trailing LF before validating.
      const out =
        raw.length > 0 && raw[raw.length - 1] === 0x0a ? raw.subarray(0, raw.length - 1) : raw

      if (isPng(out)) {
        return out
      }
    } catch {
      // No tool, no image, or a timeout: try the next command.
    }
  }

  return null
}

// PowerShell read: base64-encode the clipboard content to avoid ANSI codepage
// corruption (same problem as the write path — see comment at line 94).
const POWERSHELL_READ_ARGS = [
  '-NoProfile',
  '-NonInteractive',
  '-Command',
  '[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Clipboard -Raw)))'
] as const

type ClipboardRun = typeof execFileAsync

export function isUsableClipboardText(text: null | string): text is string {
  if (!text || !/[^\s]/.test(text)) {
    return false
  }

  if (text.includes('\u0000')) {
    return false
  }

  let suspicious = 0

  for (const ch of text) {
    const code = ch.charCodeAt(0)
    const isControl = code < 0x20 && ch !== '\n' && ch !== '\r' && ch !== '\t'

    if (isControl || ch === '\ufffd') {
      suspicious += 1
    }
  }

  return suspicious <= Math.max(2, Math.floor(text.length * 0.02))
}

function readClipboardCommands(
  platform: NodeJS.Platform,
  env: NodeJS.ProcessEnv
): Array<{ args: readonly string[]; cmd: string; base64?: boolean }> {
  if (platform === 'darwin') {
    return [{ cmd: 'pbpaste', args: [] }]
  }

  if (platform === 'win32') {
    return [{ cmd: 'powershell', args: POWERSHELL_READ_ARGS, base64: true }]
  }

  const attempts: Array<{ args: readonly string[]; cmd: string; base64?: boolean }> = []

  if (env.WSL_INTEROP || env.WSL_DISTRO_NAME) {
    attempts.push({ cmd: 'powershell.exe', args: POWERSHELL_READ_ARGS, base64: true })
  }

  if (env.WAYLAND_DISPLAY) {
    attempts.push({ cmd: 'wl-paste', args: ['--type', 'text'] })
  }

  attempts.push({ cmd: 'xclip', args: ['-selection', 'clipboard', '-out'] })

  return attempts
}

/**
 * Read plain text from the system clipboard.
 *
 * Uses native platform tools in fallback order:
 * - macOS: pbpaste
 * - Windows: PowerShell Get-Clipboard -Raw
 * - WSL: powershell.exe Get-Clipboard -Raw
 * - Linux Wayland: wl-paste --type text
 * - Linux X11: xclip -selection clipboard -out
 */
export async function readClipboardText(
  platform: NodeJS.Platform = process.platform,
  run: ClipboardRun = execFileAsync,
  env: NodeJS.ProcessEnv = process.env
): Promise<string | null> {
  for (const attempt of readClipboardCommands(platform, env)) {
    try {
      const result = await run(attempt.cmd, [...attempt.args], {
        encoding: 'utf8',
        maxBuffer: CLIPBOARD_MAX_BUFFER,
        windowsHide: true
      })

      if (typeof result.stdout === 'string') {
        if (attempt.base64) {
          return Buffer.from(result.stdout.trim(), 'base64').toString('utf8')
        }

        return result.stdout
      }
    } catch {
      // Fall through to the next clipboard backend.
    }
  }

  return null
}

// PowerShell on Windows/WSL decodes piped stdin with the system ANSI code
// page (e.g. CP936), not UTF-8, so $input-based writes mangle CJK/emoji. We
// instead base64-encode the UTF-8 bytes and pass them as a -Command argument,
// decoding with UTF8.GetString — this removes the stdin-encoding variable
// entirely (also immune to BOM injection on redirect). PowerShell entries set
// stdin=false; every other backend reads UTF-8 stdin natively.
type WriteCmd = { args: readonly string[]; cmd: string; stdin: boolean }

function _powershellWriteScript(b64: string): string {
  return `Set-Clipboard -Value ([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('${b64}')))`
}

function writeClipboardCommands(platform: NodeJS.Platform, env: NodeJS.ProcessEnv): WriteCmd[] {
  if (platform === 'darwin') {
    return [{ cmd: 'pbcopy', args: [], stdin: true }]
  }

  if (platform === 'win32') {
    return [{ cmd: 'powershell', args: ['-NoProfile', '-NonInteractive'], stdin: false }]
  }

  const attempts: WriteCmd[] = []

  if (env.WSL_INTEROP || env.WSL_DISTRO_NAME) {
    attempts.push({ cmd: 'powershell.exe', args: ['-NoProfile', '-NonInteractive'], stdin: false })
  }

  if (env.WAYLAND_DISPLAY) {
    attempts.push({ cmd: 'wl-copy', args: ['--type', 'text/plain'], stdin: true })
  }

  attempts.push({ cmd: 'xclip', args: ['-selection', 'clipboard', '-in'], stdin: true })
  attempts.push({ cmd: 'xsel', args: ['--clipboard', '--input'], stdin: true })

  return attempts
}

/**
 * Write plain text to the system clipboard.
 *
 * Tries native platform tools in fallback order:
 * - macOS: pbcopy
 * - Windows: PowerShell Set-Clipboard
 * - WSL: powershell.exe Set-Clipboard
 * - Linux Wayland: wl-copy --type text/plain
 * - Linux X11: xclip -selection clipboard -in
 * - Linux X11 alt: xsel --clipboard --input
 *
 * Returns true if at least one backend succeeded, false otherwise
 * (callers should fall back to OSC52 on false).
 */
export async function writeClipboardText(
  text: string,
  platform: NodeJS.Platform = process.platform,
  start: typeof spawn = spawn,
  env: NodeJS.ProcessEnv = process.env
): Promise<boolean> {
  const candidates = writeClipboardCommands(platform, env)

  for (const cmdEntry of candidates) {
    try {
      const ok = await new Promise<boolean>(resolve => {
        if (cmdEntry.stdin) {
          const child = start(cmdEntry.cmd, [...cmdEntry.args], {
            stdio: ['pipe', 'ignore', 'ignore'],
            windowsHide: true
          })

          child.unref()
          child.once('error', () => resolve(false))
          child.once('close', (code: number | null) => resolve(code === 0))
          child.stdin?.end(text)
        } else {
          const b64 = Buffer.from(text, 'utf8').toString('base64')
          const script = _powershellWriteScript(b64)

          const child = start(cmdEntry.cmd, [...cmdEntry.args, '-Command', script], {
            stdio: ['ignore', 'ignore', 'ignore'],
            windowsHide: true
          })

          child.unref()
          child.once('error', () => resolve(false))
          child.once('close', (code: number | null) => resolve(code === 0))
        }
      })

      if (ok) {
        return true
      }
    } catch {
      // Fall through to the next clipboard backend.
    }
  }

  return false
}
