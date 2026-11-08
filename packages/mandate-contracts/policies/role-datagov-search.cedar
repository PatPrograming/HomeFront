forbid(principal, action == Mandate::Action::"datagov_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("datagov_search") };
