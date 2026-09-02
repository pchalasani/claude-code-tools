/** Escape text so it cannot terminate an XML-like prompt envelope. */
export function escapeEnvelopeText(value: string): string {
  return value
    .replaceAll("&", "\\u0026")
    .replaceAll("<", "\\u003c")
    .replaceAll(">", "\\u003e");
}
