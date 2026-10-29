"""Who the Agent works for."""

from dataclasses import dataclass
from typing import Literal

type Role = Literal["customer", "staff", "supervisor"]


@dataclass(frozen=True)
class Person:
    """Who the Agent works for: a Customer, or a Staff member (C001, S001) and their role."""

    id: str
    role: Role = "customer"

    def introduction(self) -> str:
        """How the model is told who it works for."""
        match self.role:
            case "customer":
                return f"You are serving Customer {self.id}."
            case "staff":
                return f"You are assisting Staff member {self.id}, who helps many Customers."
            case "supervisor":
                return (
                    f"You are assisting Supervisor {self.id}, a Staff member who helps many"
                    " Customers and approves other Staff's held requests."
                )
