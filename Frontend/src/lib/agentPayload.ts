// Builds the JSON documents the forms POST to the backend for the AI agent.
import { api } from "./api.js";

export interface SocialPayload {
  type: "social_post";
  platforms: string[];
  message: string;
  links: string[];
  images: { name: string; mime_type: string; size: number; data_url: string }[];
  role: string;
  restrictions: string;
}

export interface EmailPayload {
  type: "email";
  mailboxes: string[];
  mail_platforms: string[];
  most_common_mail_type: string;
  emails_per_day: number;
  role: string;
  message: string;
  restrictions: string;
}

/**
 * Accepts "agency.gov/update", "www.agency.gov", "http://x.org" etc. and returns a full URL,
 * or null if it isn't a plausible link. Prepends "https://" when no scheme is given.
 */
export function normalizeLink(raw: string): string | null {
  const v = raw.trim();
  if (!v || /\s/.test(v)) return null;
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(v) ? v : `https://${v}`;
  try {
    const u = new URL(withScheme);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    // Require a dotted hostname (or localhost) so plain words aren't treated as links.
    if (!/\.[^.]{2,}$/.test(u.hostname) && u.hostname !== "localhost") return null;
    return u.toString();
  } catch {
    return null;
  }
}

const readAsDataUrl = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });

export async function buildSocialPayload(input: {
  platforms: string[];
  message: string;
  links: string[];
  images: File[];
  role: string;
  restrictions: string;
}): Promise<SocialPayload> {
  const images = await Promise.all(
    input.images.map(async (f) => ({
      name: f.name,
      mime_type: f.type,
      size: f.size,
      data_url: await readAsDataUrl(f),
    })),
  );
  return {
    type: "social_post",
    platforms: input.platforms,
    message: input.message.trim(),
    links: input.links.map(normalizeLink).filter((l): l is string => l !== null),
    images, // optional: empty array when nothing attached
    role: input.role,
    restrictions: input.restrictions.trim(), // optional: empty string when none
  };
}

export function buildEmailPayload(input: {
  mailboxes: string[];
  mailPlatforms: string[];
  mailType: string;
  perDay: string;
  role: string;
  message: string;
  restrictions: string;
}): EmailPayload {
  return {
    type: "email",
    mailboxes: input.mailboxes,
    mail_platforms: input.mailPlatforms,
    most_common_mail_type: input.mailType,
    emails_per_day: parseInt(input.perDay, 10),
    role: input.role,
    message: input.message.trim(),
    restrictions: input.restrictions.trim(),
  };
}

/** POSTs the JSON to the backend (route defined in api.js; the backend will hand it to the agent). */
export function sendToAgent(payload: SocialPayload | EmailPayload) {
  return api.submitAgentRequest(payload);
}
