forbid(principal, action == Mandate::Action::"email_send", resource)
  unless { principal has roleTools && principal.roleTools.contains("email_send") };
