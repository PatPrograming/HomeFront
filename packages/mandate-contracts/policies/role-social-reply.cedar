forbid(principal, action == Mandate::Action::"social_reply", resource)
  unless { principal has role && ["chief_of_staff", "communications_director"].contains(principal.role) };
