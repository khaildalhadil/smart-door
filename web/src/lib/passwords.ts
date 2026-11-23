export const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const MIN_PASSWORD = 8;
export const MAX_PASSWORD_BYTES = 72;

/** 12 characters, at least one lower-case, upper-case and digit; no look-alikes. */
export function newPassword(): string {
  const sets = ["abcdefghijkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789"];
  const letters = sets.join("");
  let out = "";
  while (!sets.every((set) => [...out].some((c) => set.includes(c)))) {
    const bytes = new Uint32Array(12);
    crypto.getRandomValues(bytes);
    out = Array.from(bytes, (b) => letters.charAt(b % letters.length)).join("");
  }
  return out;
}

export function checkPassword(value: string): void {
  if (typeof value !== "string" || value.length < MIN_PASSWORD) throw new Error("weak_password");
  if (new TextEncoder().encode(value).length > MAX_PASSWORD_BYTES) throw new Error("long_password");
}
