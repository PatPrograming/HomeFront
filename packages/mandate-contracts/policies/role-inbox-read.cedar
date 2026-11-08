forbid(principal, action == Mandate::Action::"inbox_read", resource)
  unless { principal has roleTools && principal.roleTools.contains("inbox_read") };
