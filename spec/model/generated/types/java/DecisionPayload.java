package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class DecisionPayload extends Payload {

  private String decisionId;
  private String decisionType;
  private Boolean affectsNaturalPerson;
  private Boolean legalOrSignificantEffect;
  private String aiRole;
  private List<DecisionOption> options;
  private String chosen;
  private List<String> inputs;
  private String rationaleClaimRef;
  private String oversightModality;
  private String personRef;


}