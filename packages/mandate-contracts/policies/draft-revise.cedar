permit(principal, action == Mandate::Action::"draft_revise", resource)
  when { context.delegatorActive };
