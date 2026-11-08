forbid(principal, action == Mandate::Action::"report_save", resource)
  unless { principal has roleTools && principal.roleTools.contains("report_save") };
