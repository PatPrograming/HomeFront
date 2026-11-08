export interface NavLink {
  label: string;
  href: string;
}

export const NAV_LINKS: NavLink[] = [
  { label: "Home", href: "/" },
  { label: "Emails", href: "/emails" },
  { label: "Social Platform", href: "/social-platform" },
  // HomeFront's chat console: the staffer's own assistant (mandate token, Cedar, review queue).
  { label: "Your assistant", href: "/console" },
  // The teammates' agent tabs (old-gateway agent store, behind our Cognito login).
  { label: "Agents", href: "/agents" },
  { label: "Monitoring", href: "/monitoring" },
  { label: "Approvals", href: "/approvals" },
  { label: "Activity", href: "/activity" },
];

// Shown only to the Cognito group "admin".
export const ADMIN_LINK: NavLink = { label: "Admin", href: "/admin" };

export interface Staffer {
  id: string; // Cognito username
  name: string;
  office: string;
}

// Staffers the Chief of Staff can offboard. The API has no "list staff" route yet, so this list is fixed;
// anyone else who shows up in the audit log is added on the admin page. Ids are Cognito usernames.
export const STAFFERS: Staffer[] = [{ id: "maya", name: "Maya", office: "rep-x" }];
