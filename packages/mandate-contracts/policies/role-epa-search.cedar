forbid(principal, action == Mandate::Action::"epa_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("epa_search") };
