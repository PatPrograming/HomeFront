forbid(principal, action == Mandate::Action::"eia_query", resource)
  unless { principal has roleTools && principal.roleTools.contains("eia_query") };
