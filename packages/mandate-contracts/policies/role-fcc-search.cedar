forbid(principal, action == Mandate::Action::"fcc_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("fcc_search") };
