// The key inputs from the Social platform, Email and research flows, turned into text for an agent:
// a standing brief that goes into the agent's instructions, and a first task to run right away.
import { normalizeLink } from "./agentPayload";

export const SOCIAL_ACTIONS = ["Draft", "Post/comment", "Only summarize"];
export const MAILBOXES = ["Public inbox", "Press", "FOIA", "Program-specific", "GovDelivery lists"];
export const MAIL_SYSTEMS = ["Microsoft 365/GCC", "Google Workspace", "GovDelivery", "Other"];
export const MAIL_TYPES = ["FAQs", "Complaints", "Media requests", "Social benefits"];
export const EMAIL_ACTIONS = ["Draft", "Send", "Prioritize", "Only summarize"];

export interface BriefInput {
  platforms: { id: string; name: string }[]; // social
  mailboxes: string[]; // email
  mailSystems: string[];
  mailType: string;
  perDay: string;
  action: string; // social / email: what the agent may do
  restrictions: string;
  message: string; // the first task: message, topic or research request
  links: string; // social: one per line
}

const line = (label: string, value: string) => (value.trim() ? `- ${label}: ${value.trim()}` : "");
const joined = (parts: string[]) => parts.filter(Boolean).join("\n");

/** Appended to the agent's instructions, so it applies to every run, not just the first. */
export function buildBrief(kind: string, i: BriefInput): string {
  let lines: string[] = [];
  if (kind === "social") {
    lines = [
      line("Platforms the agency is active on", i.platforms.map((p) => p.name).join(", ")),
      line("What you may do", i.action),
      line("Restrictions you must follow", i.restrictions),
    ];
  } else if (kind === "email") {
    lines = [
      line("Mailboxes you handle", i.mailboxes.join(", ")),
      line("Mailing systems in use", i.mailSystems.join(", ")),
      line("Most common mail type", i.mailType),
      line("Emails received per day", i.perDay),
      line("What you may do", i.action),
      line("Restrictions you must follow", i.restrictions),
    ];
  } else {
    lines = [line("Restrictions you must follow", i.restrictions)];
  }
  const body = joined(lines);
  return body ? `Standing brief from the person who set you up:\n${body}` : "";
}

export function parseLinks(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

export const badLinks = (raw: string) => parseLinks(raw).some((l) => normalizeLink(l) === null);

/** The run to start right after creation, or "" when no first task was given. */
export function buildFirstTask(kind: string, i: BriefInput): string {
  const message = i.message.trim();
  if (!message) return "";
  if (kind === "social") {
    const urls = parseLinks(i.links).map(normalizeLink).filter((l): l is string => l !== null);
    return joined([
      `Draft one social media post for each of these platforms: ${i.platforms.map((p) => `${p.name} (platform "${p.id}")`).join(", ")}.`,
      i.action === "Only summarize"
        ? `Do not draft or submit any posts. Reply with a short summary of the update for each platform.`
        : `Submit each draft with social.post. Adapt the wording to each platform and stay within its character limit.`,
      urls.length
        ? `Include the most relevant of these official links in every draft (Facebook and LinkedIn share only a link, so it is required there):\n${urls.map((u) => `- ${u}`).join("\n")}`
        : `No links were provided. Facebook and LinkedIn share only a link, so skip those platforms and say so.`,
      `Main message (the facts that must be communicated; do not add facts that are not here):\n${message}`,
      i.restrictions.trim() ? `Restrictions you must follow:\n${i.restrictions.trim()}` : "",
    ]).replace(/\n{3,}/g, "\n\n");
  }
  if (kind === "email") {
    return [
      `Email task. ${i.action ? `You may: ${i.action}.` : ""}`.trim(),
      `Main message (the facts that must be communicated; do not add facts that are not here):\n${message}`,
      i.restrictions.trim() ? `Restrictions you must follow:\n${i.restrictions.trim()}` : "",
    ]
      .filter(Boolean)
      .join("\n\n");
  }
  return [message, i.restrictions.trim() ? `Restrictions you must follow:\n${i.restrictions.trim()}` : ""]
    .filter(Boolean)
    .join("\n\n");
}
