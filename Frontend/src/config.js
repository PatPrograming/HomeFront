import logo from "./assets/logo.png";

export const LOGO_SRC = logo;

export const NAV_LINKS = [
  { label: "Home", href: "/" },
  { label: "Emails", href: "/emails" },
  { label: "Social Platform", href: "/social-platform" },
  { label: "Console", href: "/console" },
];

// Shown only to the Cognito group "admin".
export const ADMIN_LINK = { label: "Admin", href: "/admin" };

// Staffers the Chief of Staff can offboard. The API has no "list staff" route yet, so this list is fixed;
// anyone else who shows up in the audit log is added on the admin page. Ids are Cognito usernames.
export const STAFFERS = [{ id: "maya", name: "Maya", office: "rep-x" }];
