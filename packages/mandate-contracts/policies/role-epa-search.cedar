forbid(principal, action == Mandate::Action::"epa_search", resource)
  unless { principal has role && ["chief_of_staff", "caseworker", "communications_director", "legislative_aide"].contains(principal.role) };
