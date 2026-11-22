/**
 * Pseudonyms in the Agent's replies: the Gateway gave the Agent `<PHONE_1>` instead of a
 * Customer's phone number, so the reply names it that way. A viewer allowed to see the real
 * value reveals it at the Gateway, with their UI token (POST /gateway/reveal).
 */

/** A piece of a reply: plain text, or a Pseudonym to reveal. */
export type Piece = { text: string } | { pseudonym: string };

// The Gateway's shape: an upper-case type, an underscore, a number from 1 (pseudonyms.py).
const PSEUDONYM = /<[A-Z]+(?:_[A-Z]+)*_[1-9][0-9]*>/g;

export function splitPseudonyms(text: string): Piece[] {
  const pieces: Piece[] = [];
  let at = 0;
  for (const match of text.matchAll(PSEUDONYM)) {
    if (match.index > at) pieces.push({ text: text.slice(at, match.index) });
    pieces.push({ pseudonym: match[0] });
    at = match.index + match[0].length;
  }
  if (at < text.length) pieces.push({ text: text.slice(at) });
  return pieces;
}
