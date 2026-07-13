# Phase 2 Component Ablation Implementation Plan

1. Add default-on component switches to `AdaptiveTrendConfig`; the production
   config hash must therefore identify the explicit gate state.
2. Apply the switches only at their existing entry gates. A disabled prove-it
   hold uses a one-bar minimum so S/R remains the setup authority.
3. Lock the five scenarios in a research module and unit test their names and
   one-change-only overrides.
4. Add a clean-tree runner using the existing registry, survivability report,
   and anchored folds.
5. Run focused tests, then the full suite and real-data experiment.
6. Record findings without promoting or tuning any component.

