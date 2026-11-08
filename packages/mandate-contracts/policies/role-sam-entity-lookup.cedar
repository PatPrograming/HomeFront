forbid(principal, action == Mandate::Action::"sam_entity_lookup", resource)
  unless { principal has role && ["chief_of_staff", "legislative_aide"].contains(principal.role) };
