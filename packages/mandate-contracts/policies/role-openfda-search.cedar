forbid(principal, action == Mandate::Action::"openfda_search", resource)
  unless { principal has roleTools && principal.roleTools.contains("openfda_search") };
