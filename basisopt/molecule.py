# molecule
from typing import Any

import numpy as np
from monty.json import MSONable

from .containers import basis_to_dict, dict_to_basis
from .data import GROUNDSTATE_MULTIPLICITIES, atomic_number
from .exceptions import InvalidDiatomic
from .util import bo_logger, dict_decode


class Molecule(MSONable):
    """A very loose definition of a molecule, in that it represents
    an object with which calculations can be done.

    Attributes:
         name (str): identifier
         charge (int): overall net charge
         multiplicity (int): spin multiplicity, i.e. 2S+1
         method (str): name of calculation method, e.g. 'hf' or 'ccsd(t)'
         basis (dict): internal basis dictionary, which has (k, v) pairs
         ecps (dict): map of atom types to ecp basis names
         jbasis (dict): internal basis dictionary for Coulomb fitting set
         jkbasis (dict): internal basis dictionary for Coulomb+Exchange fitting set
         of the form (element_symbol : array of Shell objects)
         dummy_atoms (list): list of indices of atoms that should be treated as dummies

    Private attributes:
         _atom_names (list): atom symbols in order, e.g. ['H', 'H', 'O']
         _coords (list): x,y,z coords in Angstrom, as numpy arrays, same
         order as _atom_names
         _results (dict): dictionary of results calculated for this molecule.
         NOTE: these results are NOT archived, unlike for a Result object
         _references (dict): dictionary of reference values for results
    """

    def __init__(self, name: str = "Untitled", charge: int = 0, mult: int = None):
        self.name = name
        self.charge = charge
        self.multiplicity = mult
        self.method = ""
        self.basis = {}
        self.ecps = {}
        self.jbasis = None
        self.jkbasis = None
        self._atom_names = []
        self.dummy_atoms = []
        self._coords = []
        self._results = {}
        self._references = {}

    def nelectrons(self) -> int:
        """Returns the number of electrons in the molecule, not accounting for any ECPs"""
        unique = self.unique_atoms()
        nel = 0
        for a in unique:
            nel += self._atom_names.count(a) * atomic_number(a)
        return nel

    def add_atom(
        self,
        element: str = "H",
        coord: list[float] = [0.0, 0.0, 0.0],
        dummy: bool = False,
    ):
        """Adds an atom to the molecule

        Arguments:
             element (str): element name
             coord (list): [x,y,z] coords in Angstrom
             dummy (bool): if True, the atom is marked as a dummy atom
        """
        if self.multiplicity:
            self.multiplicity = self.multiplicity
        else:
            self.multiplicity = getattr(GROUNDSTATE_MULTIPLICITIES, element).value
        self._coords.append(np.array(coord))
        self._atom_names.append(element)
        if dummy:
            self.dummy_atoms.append(len(self._atom_names) - 1)

    def add_result(self, name: str, value: Any):
        """Store a result (no archiving)

        Arguments:
             name (str): identifier for result
             value (any): value of result
        """
        self._results[name] = value

    def get_result(self, name: str) -> Any:
        """Returns:
        Value of result with given name if it exists,
        otherwise 0
        """
        try:
            return self._results[name]
        except KeyError:
            return 0.0

    def add_reference(self, name: str, value: Any):
        """Same as add_result but for reference values"""
        self._references[name] = value

    def get_reference(self, name: str) -> Any:
        """Same as get_result but for reference values"""
        try:
            return self._references[name]
        except KeyError:
            return 0.0

    def get_delta(self, name: str) -> Any:
        """Returns:
        Difference between a result and its reference value
        """
        return self.get_result(name) - self.get_reference(name)

    @classmethod
    def from_xyz(
        cls, filename: str, name: str = "Untitled", charge: int = 0, mult: int = 1
    ) -> object:
        """Creates a Molecule from an xyz file

        Arguments:
             filename (str): path to xyz file
        """
        instance = cls(name=name, charge=charge, mult=mult)
        try:
            # Read in xyz file
            with open(filename, "r") as f:
                lines = f.readlines()
            # parse
            # first line should be natoms
            nat = int(lines[0])
            # second line is title
            for line in lines[2 : 2 + nat]:
                words = line.split()
                element = words[0]
                coords = np.array([float(w) for w in words[1:4]])
                instance.add_atom(element=element, coord=coords)
        except IOError as e:
            bo_logger.error("I/O error(%d): %s", e.errno, e.strerror)
        except Exception:
            bo_logger.error("Incorrect formatting in %s", filename)
        return instance

    def to_xyz(self) -> str:
        """Converts Molecule to xyz file format

        Returns:
             a string of the Molecule in xyz file format
        """
        output = f"{self.natoms()}\n{self.name}, generated by BasisOpt\n"
        for i in range(self.natoms()):
            output += self.get_line(i) + "\n"
        return output

    def get_line(self, i: int, atom_prefix: str = "", atom_suffix: str = "") -> str:
        """Gets a line of the xyz file representation of the Molecule

        Arguments:
             i (int): the index of the atom line wanted
             atom_prefix(str): optional string to add at start of atom name
                (for e.g. dummy atoms in psi4)
             atom_suffix (str): optional string to add at end of atom name
                (for e.g. dummy atoms in Orca)

        Returns:
             a string of form {prefix+element+suffix} {coords}
        """
        ix = max(i, 0)
        ix = min(ix, len(self._atom_names) - 1)
        n, c = self._atom_names[ix], self._coords[ix]
        return f"{atom_prefix}{n}{atom_suffix}\t{c[0]}\t{c[1]}\t{c[2]}"

    def natoms(self) -> int:
        """Returns number of atoms in Molecule"""
        return len(self._atom_names)

    def unique_atoms(self) -> list[str]:
        """Returns a list of all unique atom types in Molecule"""
        return list(set(self._atom_names))

    def set_ecps(self, ecp_dict: dict[str, str]):
        """Sets the ECP dictionary.

        Args:
             ecp_dict: a dictionary of atom name to ECP name. The ECP name should
                be either a name from BSE (for Psi4 backend), or the Orca internal
                library (for Orca backend, list of names can be found in the manual)
        """
        self.ecps = {k: v for k, v in ecp_dict.items() if k.title() in self._atom_names}

    def set_dummy_atoms(self, indices: list[int], overwrite: bool = True):
        """Sets the list of atoms that should be considered dummies or ghosts

        Args:
             indices: list of indices specifying which atoms to dummy-ify
             overwrite: if True, will overwrite any existing list of dummies,
                 otherwise will append to the existing list
        """
        valid_atoms = [ix for ix in indices if ix in range(self.natoms())]
        if overwrite:
            self.dummy_atoms = valid_atoms
        else:
            self.dummy_atoms.extend(valid_atoms)
        self.dummy_atoms = list(set(self.dummy_atoms))

    def get_legendre_params(self, element: str = None):
        """Returns the legendre coefficients from the basis set where available.
        By default returns all elements in the basis set unless specified.


        Parameters
        ----------
        element : str, optional
            Specific element from basis set. The default is None.

        Returns
        -------
        dict
            Dictionary of elements containing a dictionary for each angular
            momentum's legendre coefficients.

        """
        if element:
            return {shell.l: shell.leg_params for shell in self.basis[element]}
        else:
            return {
                element: {
                    shell.l: shell.leg_params[0].tolist()
                    for shell in self.basis[element]
                    if shell.leg_params
                }
                for element in self.basis.keys()
            }

    def distance(self, atom1: int, atom2: int) -> float:
        """Computes the Euclidean distance between two atoms.
        No bounds checking.

        Arguments:
             atom1, atom2 (int): indices of atoms

        Returns:
             the Euclidean separation in Angstrom
        """
        c1 = self._coords[atom1]
        c2 = self._coords[atom2]
        return np.linalg.norm(c1 - c2)

    def as_dict(self) -> dict[str, Any]:
        """Converts Molecule to MSONable dictionary

        Returns:
             dictionary representing the molecule
        """
        d = {
            "@module": type(self).__module__,
            "@class": type(self).__name__,
            "name": self.name,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "method": self.method,
            "basis": basis_to_dict(self.basis),
            "ecps": self.ecps,
            "atom_names": self._atom_names,
            "dummy_atoms": self.dummy_atoms,
            "coords": self._coords,
            "results": self._results,
            "references": self._references,
        }
        if self.jbasis:
            d["jbasis"] = basis_to_dict(self.jbasis)
        if self.jkbasis:
            d["jkbasis"] = basis_to_dict(self.jkbasis)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> object:
        """Creates a Molecule from a dictionary

        Arguments:
             d (dict): dictionary with Molecule attributes

        Returns:
             Molecule
        """
        d = dict_decode(d)
        name = d.get("name", "Untitled")
        charge = d.get("charge", 0)
        mult = d.get("multiplicity", 1)
        instance = cls(name=name, charge=charge, mult=mult)
        instance.method = d.get("method", "")
        instance.basis = dict_to_basis(d.get("basis", {}))
        instance.ecps = d.get("ecps", {})
        instance.jbasis = d.get("jbasis", None)
        instance.jkbasis = d.get("jkbasis", None)
        instance._atom_names = d.get("atom_names", [])
        instance.dummy_atoms = d.get("dummy_atoms", [])
        instance.coords = d.get("coords", [])
        instance._results = d.get("results", {})
        instance._references = d.get("references", {})
        return instance


def build_diatomic(mol_str: str, charge: int = 0, mult: int = 1) -> Molecule:
    """Builds a diatomic molecule from a string

    Arguments:
         mol_str (str): string of diatomic and separation in Angstrom
         e.g. "NO,1.3", "H2,0.9", "LiH,1.1" etc
         charge (int): net molecular charge
         mult (int): spin multiplicity

    Returns:
         Molecule object of diatomic

    Raises:
         IndexError when rval not given in mol_str
         InvalidDiatomic when mol_str can't be parsed
         error checking not exhaustive
    """
    molecule = Molecule(name=mol_str + "_Diatomic", charge=charge, mult=mult)
    # parse the mol string, form "Atom1Atom2,Separation(ang)"
    parts = mol_str.split(",")
    chars = list(parts[0])
    rval = float(parts[1])
    nchars = len(chars)
    atom1 = None
    atom2 = None
    if chars[0].isupper():
        if nchars == 2:
            # either something like NO or N2
            if chars[1] == "2":
                atom1 = atom2 = chars[0]
            elif chars[1].isupper():
                atom1 = chars[0]
                atom2 = chars[1]
        elif nchars == 3:
            if chars[2] == "2":
                # eg Ne2
                atom1 = atom2 = "".join(chars[:2])
            elif chars[1].isupper():
                # eg HLi
                atom1 = chars[0]
                atom2 = "".join(chars[1:])
            elif chars[1].islower():
                # eg LiH
                atom1 = "".join(chars[:2])
                atom2 = chars[2]
        elif nchars == 4 and chars[2].isupper():
            # eg LiCl
            atom1 = "".join(chars[:2])
            atom2 = "".join(chars[2:4])

    if (atom1 is None) or (atom2 is None):
        raise InvalidDiatomic

    molecule.add_atom(element=atom1, coord=[0.0, 0.0, -0.5 * rval])
    molecule.add_atom(element=atom2, coord=[0.0, 0.0, 0.5 * rval])
    return molecule
