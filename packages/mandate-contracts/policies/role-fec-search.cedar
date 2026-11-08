forbid(principal, action == Mandate::Action::"fec_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("fec_search") };
