forbid(principal, action == Mandate::Action::"nasa_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("nasa_search") };
