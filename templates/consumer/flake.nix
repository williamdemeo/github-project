# File: flake.nix
#
# Consumer-side wiring for the github-project roadmap engine.  `make
# init` installs this as the project's flake.nix (replacing the engine's
# own flake, which only makes sense in the engine repository).
#
# The engine arrives as a flake input, pinned by this project's
# flake.lock: `nix develop` puts the gh-project-* CLIs on PATH (the
# Makefile stub finds them there), and the re-exported apps allow
# `nix run .#update` directly.  Upgrade deliberately with
# `nix flake update github-project`.
#
# If your project already has a flake, merge the input and the two
# output attributes into it instead of replacing it.
{
  description = "Project dev shell with the github-project roadmap engine";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  inputs.github-project.url = "github:williamdemeo/github-project";

  outputs = { self, nixpkgs, github-project }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems
        (system: f system nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (system: pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.gnumake
            pkgs.gh
            github-project.packages.${system}.gh-project-engine
          ];
        };
      });

      # `nix run .#update -- docs/GITHUB_PROJECT.md`, pinned by this
      # project's own flake.lock.
      apps = forAllSystems (system: pkgs: github-project.apps.${system});
    };
}
