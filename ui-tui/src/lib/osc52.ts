const ESC = '\x1b'
const BEL = '\x07'
const ST = `${ESC}\\`

export const OSC52_CLIPBOARD_QUERY = `${ESC}]52;c;?${BEL}`

type OscResponse = { code: number; data: string; type: 'osc' }

type OscQuerier = {
  flush: () => Promise<void>
  send: <T>(query: { match: (r: unknown) => r is T; request: string }) => Promise<T | undefined>
}

function wrapForMultiplexer(sequence: string): string {
  if (process.env['TMUX']) {
    return `${ESC}Ptmux;${sequence.split(ESC).join(ESC + ESC)}${ST}`
  }

  if (process.env['STY']) {
    return `${ESC}P${sequence}${ST}`
  }

  return sequence
}

export function buildOsc52ClipboardQuery(): string {
  return wrapForMultiplexer(OSC52_CLIPBOARD_QUERY)
}

export function parseOsc52ClipboardData(data: string): null | string {
  const firstSep = data.indexOf(';')

  if (firstSep === -1) {
    return null
  }

  const selection = data.slice(0, firstSep)
  const payload = data.slice(firstSep + 1)

  if ((selection !== 'c' && selection !== 'p') || !payload || payload === '?') {
    return null
  }

  try {
    return Buffer.from(payload, 'base64').toString('utf8')
  } catch {
    return null
  }
}

export async function readOsc52Clipboard(querier: null | OscQuerier, timeoutMs = 500): Promise<null | string> {
  if (!querier) {
    return null
  }

  const timeout = new Promise<void>(resolve => setTimeout(resolve, timeoutMs))

  const query = querier.send<OscResponse>({
    request: buildOsc52ClipboardQuery(),
    match: (r: unknown): r is OscResponse => {
      return !!r && typeof r === 'object' && (r as OscResponse).type === 'osc' && (r as OscResponse).code === 52
    }
  })

  const response = await Promise.race([query, timeout])

  // The DA1 sentinel flush() waits on has no deadline of its own ("never times
  // out on its own" is the querier's contract), so a terminal that never
  // answers DA1 would hang every clipboard read past the query's race. Bound
  // it: a read is advisory, the caller falls through to the next source.
  await Promise.race([querier.flush(), timeout])

  return response ? parseOsc52ClipboardData(response.data) : null
}

export const writeOsc52Clipboard = (s: string) => {
  const sequence = `${ESC}]52;c;${Buffer.from(s, 'utf8').toString('base64')}${BEL}`

  return process.stdout.write(wrapForMultiplexer(sequence))
}
