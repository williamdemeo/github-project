# File: flake.nix
#
# Strictly optional convenience: `nix develop` drops you into a shell
# with everything the Makefile needs (python3, gh, gnumake), so a
# machine without Python can still run the tooling.  Nothing in this
# repository requires Nix — the plain path is documented first in the
# README, and CI uses setup-python, so consumers are never Nix-bound.
{
  description = "Dev shell for the github-project template (python3, gh, make)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems
        (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.gh pkgs.gnumake ];
        };
      });
    };
}
