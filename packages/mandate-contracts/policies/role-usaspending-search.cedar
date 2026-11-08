forbid(principal, action == Mandate::Action::"usaspending_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("usaspending_search") };
