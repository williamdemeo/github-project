# File: flake.nix
#
# The engine's single home (issue #2).  Outputs:
#
#   packages.default        the engine: gh-project-{populate,update,lint}
#                           CLIs wrapping the stdlib-only scripts, with
#                           python3 and gh on their runtime PATH
#   apps.{populate,update,update-check,lint}
#                           `nix run .#update -- docs/GITHUB_PROJECT.md`
#   checks.engine-tests     the offline unit/integration suite
#   devShells.default       hacking on this repository
#
# Consumers add this repository as a flake input, put the package in
# their dev shell (or re-export the apps), and pin it in their
# flake.lock — upgrades happen deliberately via `nix flake update
# github-project`.  Nothing here is required to USE the engine: it is
# stdlib-only Python, so a plain checkout plus python3 works too (see
# the README's consumption channels).
{
  description = "github-project engine: populate, update, and lint a GitHub roadmap from one plan file";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems
        (system: f system nixpkgs.legacyPackages.${system});
      version = nixpkgs.lib.trim (builtins.readFile ./scripts/VERSION);
    in
    {
      packages = forAllSystems (system: pkgs: rec {
        default = gh-project-engine;
        gh-project-engine = pkgs.stdenv.mkDerivation {
          pname = "gh-project-engine";
          inherit version;
          src = ./scripts;
          # python3 must be on the check-phase PATH: the recorded fake
          # `gh` the integration tests exec is itself a python3 script.
          nativeBuildInputs = [ pkgs.makeWrapper pkgs.python3 ];
          dontBuild = true;

          # The suite is offline by design (recorded fake `gh`), so it
          # can run inside the sandbox; `nix flake check` gets teeth.
          doCheck = true;
          checkPhase = ''
            ${pkgs.python3.interpreter} -m unittest discover -s tests
          '';

          installPhase = ''
            lib=$out/lib/gh-project-engine
            mkdir -p $lib $out/bin
            cp gh_project_populate.py gh_project_update.py \
               gh_project_lint.py _gh_project_lib.py VERSION $lib/
            cp -r _utils $lib/_utils
            for tool in populate update lint; do
              makeWrapper ${pkgs.python3.interpreter} $out/bin/gh-project-$tool \
                --add-flags "$lib/gh_project_$tool.py" \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.gh ]}
            done
          '';

          meta = {
            description = "Populate, update, and lint a GitHub roadmap from one plan file";
            homepage = "https://github.com/williamdemeo/github-project";
            license = pkgs.lib.licenses.mit;
            mainProgram = "gh-project-update";
          };
        };
      });

      apps = forAllSystems (system: pkgs:
        let
          engine = self.packages.${system}.gh-project-engine;
          app = exe: {
            type = "app";
            program = "${engine}/bin/${exe}";
          };
          updateCheckBin = pkgs.writeShellApplication {
            name = "gh-project-update-check";
            text = ''exec ${engine}/bin/gh-project-update --check "$@"'';
          };
        in
        rec {
          populate = app "gh-project-populate";
          update = app "gh-project-update";
          lint = app "gh-project-lint";
          update-check = {
            type = "app";
            program = "${updateCheckBin}/bin/gh-project-update-check";
          };
          default = update;
        });

      checks = forAllSystems (system: pkgs: {
        engine-tests = self.packages.${system}.gh-project-engine;
      });

      devShells = forAllSystems (system: pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.gh pkgs.gnumake ];
        };
      });
    };
}
