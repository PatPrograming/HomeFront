forbid(principal, action == Mandate::Action::"datagov_search", resource)
  unless { principal has role && ["chief_of_staff", "caseworker", "communications_director", "legislative_aide", "intern"].contains(principal.role) };
