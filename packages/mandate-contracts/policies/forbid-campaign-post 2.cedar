forbid(principal, action == Mandate::Action::"social_post", resource)
  when { principal.mode == "official" && context.account == "campaign" };
