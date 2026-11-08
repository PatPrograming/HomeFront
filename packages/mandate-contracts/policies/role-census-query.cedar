forbid(principal, action == Mandate::Action::"census_query", resource)
  unless { principal has roleTools && principal.roleTools.contains("census_query") };
