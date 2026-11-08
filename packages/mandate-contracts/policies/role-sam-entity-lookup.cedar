forbid(principal, action == Mandate::Action::"sam_entity_lookup", resource)
  unless { principal has roleTools && principal.roleTools.contains("sam_entity_lookup") };
