forbid(principal, action == Mandate::Action::"social_post", resource)
  unless { principal has role && ["chief_of_staff", "communications_director"].contains(principal.role) };
