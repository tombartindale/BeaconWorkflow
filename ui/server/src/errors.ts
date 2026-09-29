// Errors that map onto HTTP answers, as the Python backend's exceptions did.

/** A request the backend refuses to act on: 400. */
export class BadRequest extends Error {}
/** A path outside the programme root, or anywhere else the request may not reach: 403. */
export class Forbidden extends Error {
  constructor(message = 'That path is outside the programme root.') { super(message); }
}
/** bcn could not run, or printed no envelope: 502. */
export class BcnError extends Error {}
/** The programme root is not usable; the server refuses to start. */
export class StartupError extends Error {}
