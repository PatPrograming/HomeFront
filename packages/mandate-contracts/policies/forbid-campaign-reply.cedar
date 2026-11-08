forbid(principal, action == Mandate::Action::"social_reply", resource)
  when { principal.mode == "official" && context.account == "campaign" };
