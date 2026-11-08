forbid(principal, action == Mandate::Action::"inbox_read", resource)
  unless { principal has role && ["chief_of_staff", "caseworker", "communications_director", "legislative_aide"].contains(principal.role) };
