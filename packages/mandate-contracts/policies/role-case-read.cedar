forbid(principal, action == Mandate::Action::"case_read", resource)
  unless { principal has roleTools && principal.roleTools.contains("case_read") };
