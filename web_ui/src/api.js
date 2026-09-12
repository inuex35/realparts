// One session over POST /op: the line server's request and reply, as JSON.
export class OpError extends Error {
  constructor(reply) { super(reply.message || "error"); this.kind = reply.kind; this.detail = reply.detail; }
}

export async function op(name, args = {}) {
  const reply = await (await fetch("/op", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op: name, ...args }),
  })).json();
  if (!reply.ok) throw new OpError(reply);
  return reply.result;
}

export const get = async (path) => (await fetch(path)).json();
export const post = async (path, body) => (await fetch(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
